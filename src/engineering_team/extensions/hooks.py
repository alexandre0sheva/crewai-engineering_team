"""Lifecycle hooks: commands and webhooks run at ``before_stage``, ``after_stage``, ``on_finish``.

Hooks are for notifications and CI glue (a Slack message, a script that records the run). They
are configuration, so they run with your privileges on this machine whatever the execution
backend is, and they can never change the run: a hook that fails, times out, or cannot start is a
``hook.ran`` event with ``ok: false`` and nothing else. Each is bounded by its ``timeout_seconds``;
a cancelled run still runs its ``on_finish`` hooks. Payloads and output are scrubbed of secret
values, and a webhook address (often a secret itself) is never written to the event log.

A webhook gets a JSON POST: ``{"text": "...", "event": ..., "run_id": ..., "project": ...}``
plus ``stage``/``status`` where they apply; ``text`` is a one-line summary, which is what
Slack-style incoming webhooks display. A command gets the same facts as ``ENGINEERING_HOOK_*``
environment variables (``ENGINEERING_HOOK_PAYLOAD`` is the whole JSON) and runs, without a shell,
in the directory the team was started from.
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from engineering_team.execution.backend import CommandSpec, ExecutionBackend
from engineering_team.execution.local import LocalBackend
from engineering_team.extensions.config import Hook
from engineering_team.runtime.events import Scrubber
from engineering_team.settings import child_environment, secret_values

if TYPE_CHECKING:
    from engineering_team.contracts import RunSummary
    from engineering_team.runtime.context import RunContext

Poster = Callable[[str, Mapping[str, Any], float], int]
PASSED_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER")
MAX_DETAIL = 300
MAX_TEXT = 300


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a webhook that redirects is misconfigured; the 3xx is the failure


def post_json(url: str, payload: Mapping[str, Any], timeout: float) -> int:
    """POST ``payload`` as JSON; returns the status. Raises for any non-2xx or network error."""

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "engineering-team"},
        method="POST",
    )
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
        response.read(1024)
        return int(response.status)


class HookRunner:
    """Runs the hooks of one run. Nothing here raises (except ``KeyboardInterrupt``)."""

    def __init__(
        self,
        ctx: RunContext,
        *,
        poster: Poster = post_json,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self.ctx = ctx
        self._poster = poster
        self._backend = backend
        self._scrubber = Scrubber(secret_values())

    @property
    def active(self) -> bool:
        return self.ctx.settings.hooks.any

    # -- the three events ---------------------------------------------------------------------

    def before_stage(self, stage: str) -> None:
        text = f"stage '{stage}' started"
        self._fire("before_stage", self.ctx.settings.hooks.before_stage, text, stage=stage)

    def after_stage(self, stage: str, status: str, detail: str = "") -> None:
        text = f"stage '{stage}' {status}" + (f": {detail}" if detail else "")
        self._fire(
            "after_stage",
            self.ctx.settings.hooks.after_stage,
            text,
            stage=stage,
            status=status,
            **({"error": detail} if detail and status == "failed" else {}),
        )

    def on_finish(self, status: str, error: str | None, summary: RunSummary | None) -> None:
        extra: dict[str, Any] = {}
        text = f"run {self.ctx.run_id} {status}"
        if error:
            extra["error"] = error
            text += f": {error}"
        if summary is not None:
            extra["duration_seconds"] = summary.duration_seconds
            if summary.estimated_cost_usd is not None:
                extra["estimated_cost_usd"] = summary.estimated_cost_usd
        self._fire("on_finish", self.ctx.settings.hooks.on_finish, text, status=status, **extra)

    # -- running one ----------------------------------------------------------------------------

    def _fire(
        self, event: str, hooks: list[Hook], text: str, *, stage: str | None = None, **facts: Any
    ) -> None:
        status = facts.get("status")
        chosen = [
            hook
            for hook in hooks
            if (not hook.stages or stage in hook.stages)
            and (not hook.statuses or status in hook.statuses)
        ]
        if not chosen:
            return
        settings = self.ctx.settings
        payload = self._scrubber.scrub(
            {
                "text": f"[engineering-team] {settings.project_name}: {text}"[:MAX_TEXT],
                "event": event,
                "run_id": self.ctx.run_id,
                "project": settings.project_name,
                **({"stage": stage} if stage else {}),
                **facts,
            }
        )
        for hook in chosen:
            self._run(event, hook, payload)

    def _run(self, event: str, hook: Hook, payload: dict[str, Any]) -> None:
        started = time.monotonic()
        kind = "command" if hook.command is not None else "webhook"
        target = hook.command[0] if hook.command else _host(hook.url or "")
        try:
            detail = (
                self._command(event, hook, payload)
                if hook.command is not None
                else self._webhook(hook, payload)
            )
            ok = True
        except Exception as exc:  # a hook never affects the run
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.ctx.events.emit(
            "hook.ran",
            hook=event,
            kind=kind,
            target=target,
            ok=ok,
            duration=round(time.monotonic() - started, 3),
            detail=self._scrubber.scrub(detail.replace(hook.url or "\0", "<url>"))[:MAX_DETAIL],
            **({"stage": payload["stage"]} if "stage" in payload else {}),
        )

    def _webhook(self, hook: Hook, payload: dict[str, Any]) -> str:
        assert hook.url is not None
        status = self._poster(hook.url, payload, float(hook.timeout_seconds))
        if not 200 <= status < 300:
            raise RuntimeError(f"the webhook answered HTTP {status}")
        return f"HTTP {status}"

    def _command(self, event: str, hook: Hook, payload: dict[str, Any]) -> str:
        assert hook.command is not None
        passed = child_environment()
        env = {name: passed[name] for name in PASSED_ENVIRONMENT if name in passed}
        env["ENGINEERING_HOOK_PAYLOAD"] = json.dumps(payload)
        env.update(
            {
                f"ENGINEERING_HOOK_{key.upper()}": str(value)
                for key, value in payload.items()
                if isinstance(value, str | int | float)
            }
        )
        spec = CommandSpec(
            argv=tuple(hook.command),
            cwd=Path.cwd(),
            env=env,
            timeout=float(hook.timeout_seconds),
            label=f"hook-{event}",
        )
        with tempfile.TemporaryDirectory(prefix="engineering-team-hook-") as scratch:
            backend = self._backend or LocalBackend(Path(scratch))
            record = backend.run(spec)
        tail = " ".join(record.output_tail.split())[-MAX_DETAIL:]
        if record.timed_out:
            raise RuntimeError(f"timed out after {hook.timeout_seconds}s. {tail}".strip())
        if record.exit_code != 0:
            raise RuntimeError(f"exit code {record.exit_code}. {tail}".strip())
        return f"exit code 0. {tail}".strip()


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "?"
    except ValueError:
        return "?"
