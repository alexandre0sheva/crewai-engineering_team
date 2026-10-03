"""``Verifier``: the controller runs each check itself and records what it saw.

Nothing here asks an agent whether something passes. A check is a command (or, for a detected
test/lint/type-check/build default, the structured developer tool for the project) run through
the run's execution backend; its exit code, duration, log, and the workspace revision it ran
against are stored. Statuses are distinct: ``passed``, ``failed``, ``skipped`` (not run because
a required setup failed), and ``unavailable`` (it could not run: a missing runtime or a command
the controller may not run), which is never read as a pass.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import CheckResult, CheckSpec, CheckStatus, utc_now
from engineering_team.devtools.availability import install_hint
from engineering_team.devtools.models import DevReport, DiagnosticReport, TestReport
from engineering_team.devtools.plans import Selection
from engineering_team.devtools.render import render_diagnostics, render_tests
from engineering_team.devtools.runner import DevRunner
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import Scrubber
from engineering_team.settings import secret_values
from engineering_team.tools.support import ToolError
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, WorkspaceError
from engineering_team.verification.cards import CheckCards
from engineering_team.verification.revision import verification_revision

MAX_SUSPECTS = 10
MAX_TAIL = 2500
LOCATION = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,5})[:(](\d+)")
PLAYWRIGHT_MISSING = (
    "No module named 'playwright'",
    "Executable doesn't exist",
    "playwright install",
    "Cannot find module '@playwright/test'",
)
PLAYWRIGHT_HINT = (
    "Playwright (or its browser) is not installed for this project: install it "
    "(`pip install playwright` or `npm i -D @playwright/test`), then run "
    "`playwright install chromium`."
)
JS_SCRIPTS = (".js", ".mjs", ".cjs", ".ts")


class Verifier:
    """Runs checks as the controller. ``cards`` (optional) shows each one on the task board;
    ``script_digests`` are the pinned hashes of the user's browser scripts."""

    def __init__(
        self,
        ctx: RunContext,
        *,
        cards: CheckCards | None = None,
        script_digests: Mapping[str, str] | None = None,
    ) -> None:
        self.ctx = ctx
        self.cards = cards
        self.script_digests = dict(script_digests or {})
        self.runner = DevRunner(ctx)
        self._scrub = Scrubber(secret_values())

    # -- running ---------------------------------------------------------------------------

    def run(self, checks: Sequence[CheckSpec], *, round: int = 0) -> list[CheckResult]:
        """Run ``checks`` in order and return one result each, all stamped with the revision of
        the workspace they left (a check that writes into the project moves it, so it is taken
        after the last one). The batch is also written to ``verification/round-<round>.json``."""

        results: list[CheckResult] = []
        blocked_by: CheckSpec | None = None
        for check in checks:
            check_cancelled(self.ctx)
            if blocked_by is not None and check.kind != "setup":
                results.append(self._skipped(check, blocked_by))
                continue
            result = self._one(check)
            results.append(result)
            if (
                check.kind == "setup"
                and check.required
                and result.status in ("failed", "unavailable")
            ):
                blocked_by = check
        revision = verification_revision(self.ctx.workspace)
        results = [result.model_copy(update={"revision": revision}) for result in results]
        atomic_write_json(
            self.ctx.run_dir / "verification" / f"round-{round}.json",
            {
                "round": round,
                "revision": revision,
                "results": [result.model_dump(mode="json") for result in results],
            },
        )
        return results

    def _one(self, check: CheckSpec) -> CheckResult:
        ctx = self.ctx
        started = utc_now()
        began = time.monotonic()
        ctx.events.emit("check.started", check=check.id, name=check.name, kind=check.kind)
        if self.cards is not None:
            self.cards.start(check)
        try:
            result = self._execute(check)
        except RunCancelled:
            if self.cards is not None:
                self.cards.cancel(check, "the run was cancelled")
            raise
        result = result.model_copy(update={"started_at": started})
        if not result.duration:
            result = result.model_copy(update={"duration": round(time.monotonic() - began, 2)})
        result = result.model_copy(
            update={"summary": self._scrub.scrub(result.summary), "log_tail": result.log_tail}
        )
        if self.cards is not None:
            self.cards.finish(check, result)
        ctx.events.emit(
            "check.finished",
            check=check.id,
            status=result.status,
            exit_code=result.exit_code,
            duration=result.duration,
            log_path=result.log_path,
            summary=result.summary,
        )
        return result

    def _skipped(self, check: CheckSpec, blocked_by: CheckSpec) -> CheckResult:
        result = self._result(check, "skipped").model_copy(
            update={
                "summary": "not run",
                "hint": f"Not run because the required {blocked_by.kind} check "
                f"'{blocked_by.id}' did not pass; fix that first.",
            }
        )
        if self.cards is not None:
            self.cards.start(check)
            self.cards.finish(check, result)
        self.ctx.events.emit("check.finished", check=check.id, status="skipped")
        return result

    @staticmethod
    def _result(check: CheckSpec, status: CheckStatus, **fields: object) -> CheckResult:
        return CheckResult(
            id=check.id,
            status=status,
            name=check.name,
            kind=check.kind,
            required=check.required,
            source=check.source,
            criteria_ids=list(check.criteria_ids),
            **fields,  # type: ignore[arg-type]
        )

    # -- the three ways a check runs ---------------------------------------------------------

    def _execute(self, check: CheckSpec) -> CheckResult:
        try:
            if check.type == "browser_script":
                return self._browser_script(check)
            if check.source == "detected" and check.kind in ("test", "lint", "typecheck", "build"):
                return self._detected(check)
            return self._command(check, list(check.argv))
        except WorkspaceError as exc:  # a command the controller may not run, or no such directory
            return self._result(
                check,
                "unavailable",
                summary="could not be run",
                hint=f"The controller cannot run this check: {exc}",
            )
        except ToolError as exc:
            check_cancelled(self.ctx)
            return self._result(check, "failed", summary=str(exc)[:200], hint=str(exc))

    def _command(self, check: CheckSpec, argv: list[str]) -> CheckResult:
        runner = self.runner
        directory = self.ctx.workspace.resolve(check.cwd, must_exist=True)
        allow = [Path(argv[0]).name] if check.source == "user" else []
        timeout = max(1, int(check.timeout))
        ex = runner.execute(argv, directory, timeout, network=check.kind == "setup", allow=allow)
        command = runner.display(ex.command)
        if ex.tool_missing:
            return self._result(
                check,
                "unavailable",
                command=command,
                summary=f"{ex.tool_missing} is not installed",
                hint=install_hint(ex.tool_missing, None),
            )
        record = ex.record
        assert record is not None
        status: CheckStatus = "passed" if record.exit_code == 0 else "failed"
        summary = f"exit code {record.exit_code}"
        if record.timed_out:
            if check.kind == "smoke":
                status, summary = "passed", f"still running after {timeout}s"
            else:
                status, summary = "failed", f"timed out after {timeout}s"
        elif check.kind == "smoke" and status == "passed":
            summary = "ran and exited cleanly"
        tail = "" if status == "passed" else self._scrub.scrub(ex.tail)
        return self._result(
            check,
            status,
            command=command,
            exit_code=record.exit_code,
            duration=round(record.duration, 2),
            log_path=ex.log,
            summary=summary,
            log_tail=tail,
            suspect_files=self._suspects(tail),
        )

    def _detected(self, check: CheckSpec) -> CheckResult:
        runner = self.runner
        report: TestReport | DiagnosticReport
        if check.kind == "test":
            report = runner.run_tests(check.cwd, Selection())
        elif check.kind == "lint":
            report = runner.lint(check.cwd)
        elif check.kind == "typecheck":
            report = runner.typecheck(check.cwd)
        else:
            report = runner.build(check.cwd)
        return self._from_report(check, report)

    def _from_report(self, check: CheckSpec, report: TestReport | DiagnosticReport) -> CheckResult:
        status: CheckStatus
        if report.status == "passed":
            status = "passed"
        elif report.status == "unavailable":
            status = "unavailable"
        else:  # failed, error (it ran but showed nothing usable), unknown: never a pass
            status = "failed"
        if isinstance(report, TestReport):
            summary = f"{report.passed} passed, {report.failed} failed"
            if report.errors:
                summary += f", {report.errors} errors"
            summary += f", {report.skipped} skipped"
            files = [f.file for f in report.failures if f.file]
            text = render_tests(report)
        else:
            summary = f"{report.errors} error(s), {report.warnings} warning(s)"
            files = [d.file for d in report.diagnostics if d.file]
            text = render_diagnostics(report)
        if status != "passed" and report.hint and report.status != "failed":
            summary = report.hint.splitlines()[0][:200]
        return self._result(
            check,
            status,
            command=report.command,
            exit_code=report.exit_code,
            duration=report.duration,
            log_path=report.log_path,
            summary=summary,
            hint=report.hint,
            log_tail="" if status == "passed" else self._scrub.scrub(text[-MAX_TAIL:]),
            suspect_files=list(dict.fromkeys(files))[:MAX_SUSPECTS],
            report=self._dump(report),
        )

    @staticmethod
    def _dump(report: DevReport) -> dict[str, object]:
        return report.model_dump(mode="json")

    def _browser_script(self, check: CheckSpec) -> CheckResult:
        script = check.script or ""
        path = self.ctx.workspace.root / script
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return self._result(
                check,
                "failed",
                summary="script not found",
                hint=f"The script {script} does not exist in the project.",
            )
        pinned = self.script_digests.get(script)
        if pinned is not None and pinned != digest:
            return self._result(
                check,
                "failed",
                summary="script altered",
                hint=f"The script {script} changed since the run started, so it proves nothing. "
                "Restore it, or start a new run.",
            )
        if path.suffix == ".py":
            argv = [self._python(), script]
        elif path.suffix in JS_SCRIPTS:
            argv = ["npx", "--no-install", "playwright", "test", script]
        else:
            return self._result(
                check,
                "failed",
                summary="unsupported script",
                hint=f"A browser_script must be a .py or {'/'.join(JS_SCRIPTS)} Playwright test.",
            )
        result = self._command(check, argv)
        if result.status == "failed" and any(m in result.log_tail for m in PLAYWRIGHT_MISSING):
            return result.model_copy(
                update={
                    "status": "unavailable",
                    "hint": PLAYWRIGHT_HINT,
                    "summary": "Playwright missing",
                }
            )
        return result

    def _python(self) -> str:
        return "python" if shutil.which("python") else "python3"

    # -- helpers ---------------------------------------------------------------------------

    def _suspects(self, text: str) -> list[str]:
        """Project files a failure's output points at (``path:line``), most mentioned first."""

        root = self.ctx.workspace.root
        found: dict[str, int] = {}
        for match in LOCATION.finditer(text):
            name = match.group(1).removeprefix("./")
            candidate = Path(name)
            if candidate.is_absolute():
                try:
                    name = candidate.relative_to(root).as_posix()
                except ValueError:
                    continue
            if name.startswith(CONTROLLER_DIRECTORY) or not (root / name).is_file():
                continue
            found[name] = found.get(name, 0) + 1
        ranked = sorted(found, key=lambda n: (-found[n], n))
        return ranked[:MAX_SUSPECTS]
