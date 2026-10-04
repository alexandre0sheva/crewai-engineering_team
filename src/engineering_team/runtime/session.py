"""One run's record: manifest, request, settings, and the lifecycle around its work."""

from __future__ import annotations

import contextlib
import hashlib
import json
import platform
from collections.abc import Iterator
from importlib import metadata

import crewai

from engineering_team.atomic_io import atomic_write_json, atomic_write_text
from engineering_team.contracts import (
    RunManifest,
    RunStatus,
    RunSummary,
    RunVerdict,
    StageRecord,
    utc_now,
)
from engineering_team.execution.backend import close_backend
from engineering_team.extensions.hooks import HookRunner
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import stage_scope
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.snapshot import workspace_revision
from engineering_team.settings import redacted_dump


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _versions() -> dict[str, str]:
    try:
        own = metadata.version("engineering_team")
    except metadata.PackageNotFoundError:  # running from an unbuilt source tree
        own = "unknown"
    return {
        "engineering_team": own,
        "crewai": crewai.__version__,
        "python": platform.python_version(),
    }


class RunRecorder:
    """Creates a run's manifest and files, then tracks it through ``pending → running → end``.

    ``begin`` writes ``manifest.json`` (pending), ``request.md``, and ``settings.json``.
    ``running()`` wraps the work: it moves the run to ``running``, binds CrewAI's events to
    the run's event log, and on exit records the outcome (``succeeded``; ``cancelled`` if the
    run's cancel event is set; ``interrupted`` for Ctrl-C; ``failed`` otherwise). A strategy
    that fails without raising calls :meth:`fail`. ``reopen`` attaches to an existing run so
    ``running()`` can continue it (resume).
    """

    def __init__(self, ctx: RunContext, store: RunStore) -> None:
        self.ctx = ctx
        self.store = store
        self.hooks = HookRunner(ctx)
        self._failure: str | None = None

    @classmethod
    def begin(
        cls,
        ctx: RunContext,
        *,
        mode: str = "build",
        request: str = "",
        strategy: str = "hierarchical",
        recipe: str | None = None,
    ) -> RunRecorder:
        store = RunStore(ctx.workspace.root)
        canonical = json.dumps(ctx.settings.model_dump(mode="json"), sort_keys=True, default=str)
        settings_payload = redacted_dump(ctx.settings)  # the record never holds a webhook address
        atomic_write_text(ctx.run_dir / "request.md", request.rstrip() + "\n")
        atomic_write_json(ctx.run_dir / "settings.json", settings_payload)
        store.create(
            RunManifest(
                run_id=ctx.run_id,
                project_name=ctx.settings.project_name,
                mode=mode,
                request_hash=_sha256(request.strip()),
                settings_hash=_sha256(canonical),
                versions=_versions(),
                strategy=strategy,
                recipe=recipe,
            )
        )
        return cls(ctx, store)

    @classmethod
    def attach(cls, ctx: RunContext) -> RunRecorder:
        """The recorder of a run whose manifest already exists."""

        return cls(ctx, RunStore(ctx.workspace.root))

    @classmethod
    def reopen(cls, ctx: RunContext) -> RunRecorder:
        """Prepare an unfinished or failed run to continue (``running()`` then reopens it).

        The caller holds the workspace lock, so a manifest still saying ``running`` belongs to
        a process that died: it becomes ``interrupted``, as does each of its running stages.
        """

        recorder = cls.attach(ctx)
        store = recorder.store
        if store.load(ctx.run_id).status == "running":
            store.set_status(ctx.run_id, "interrupted")

        def change(manifest: RunManifest) -> None:
            manifest.resumes += 1
            for stage in manifest.stages:
                if stage.status == "running":
                    stage.status = "interrupted"

        store.update(ctx.run_id, change)
        return recorder

    @property
    def manifest(self) -> RunManifest:
        return self.store.load(self.ctx.run_id)

    def set_verdict(self, verdict: RunVerdict) -> None:
        """Record how the run's verification ended (``verified``, ``failed``, ``partial``, or
        ``needs-info``) in the manifest."""

        def change(manifest: RunManifest) -> None:
            manifest.verdict = verdict

        self.store.update(self.ctx.run_id, change)

    def fail(self, error: str) -> None:
        """End the run as ``failed`` with this message, without raising."""

        self._failure = error

    @contextlib.contextmanager
    def running(self) -> Iterator[RunRecorder]:
        ctx = self.ctx
        self._failure = None
        manifest = self.store.set_status(ctx.run_id, "running")
        ctx.events.emit(
            "run.started",
            mode=manifest.mode,
            project=ctx.settings.project_name,
            **({"resumed": manifest.resumes} if manifest.resumes else {}),
        )
        outcome: RunStatus = "succeeded"
        error: str | None = None
        try:
            with bind_run(ctx.run_id, ctx.events):
                yield self
            flush_bridge()  # deliver CrewAI's in-flight events, then the last safe point:
            ctx.budget.check()  # raises BudgetExceeded if the run overspent
            if self._failure is not None:
                outcome, error = "failed", self._failure
        except KeyboardInterrupt:
            outcome = "interrupted"
            raise
        except BaseException as exc:
            outcome, error = "failed", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # A budget stop sets the cancel event too, but it is a failure, not a cancellation.
            if (
                outcome in ("succeeded", "failed")
                and ctx.cancel_event.is_set()
                and ctx.budget.tripped is None
            ):
                outcome = "cancelled"
            flush_bridge()
            self._finish(outcome, error)

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Run one stage: events, the stage tag on everything inside it, a record in the
        manifest (with the workspace revision at both ends), and a budget check at both
        boundaries. :class:`RunCancelled` ends it ``cancelled``; Ctrl-C ends it ``interrupted``."""

        ctx = self.ctx
        ctx.budget.check()
        attempts = next((s.attempts for s in self.manifest.stages if s.name == name), 0) + 1
        record = StageRecord(
            name=name,
            status="running",
            started=utc_now(),
            attempts=attempts,
            revision_start=workspace_revision(ctx.workspace),
        )
        self.store.record_stage(ctx.run_id, record)
        ctx.events.emit("stage.started", stage=name, attempt=attempts)
        status: str = "succeeded"
        detail = ""
        try:
            self.hooks.before_stage(name)
            with stage_scope(name):
                yield
            flush_bridge()
            ctx.budget.check()
        except KeyboardInterrupt:
            status = "interrupted"
            raise
        except RunCancelled as exc:
            status, detail = "cancelled", str(exc)
            raise
        except BaseException as exc:
            status, detail = "failed", f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            ctx.processes.stop_stage(name)  # a server started in this stage does not outlive it
            ctx.browsers.stop_stage(name)  # nor does a browser context opened in it
            record = record.model_copy(
                update={
                    "status": status,
                    "finished": utc_now(),
                    "revision": workspace_revision(ctx.workspace),
                    "detail": detail,
                }
            )
            self.store.record_stage(ctx.run_id, record)
            ctx.events.emit("stage.finished", stage=name, status=status)
            self.hooks.after_stage(name, status, detail)

    def skip_stage(self, name: str, reason: str) -> None:
        """Record a stage that was not needed (its revision is the unchanged workspace's)."""

        revision = workspace_revision(self.ctx.workspace)
        now = utc_now()
        attempts = next((s.attempts for s in self.manifest.stages if s.name == name), 0)
        self.store.record_stage(
            self.ctx.run_id,
            StageRecord(
                name=name,
                status="skipped",
                started=now,
                finished=now,
                attempts=attempts,
                revision_start=revision,
                revision=revision,
                detail=reason,
            ),
        )
        self.ctx.events.emit("stage.skipped", stage=name, reason=reason)

    def summary(self, status: RunStatus) -> RunSummary:
        """What the run used and cost so far, with its standing against the budget."""

        ctx = self.ctx
        report = ctx.usage.report(ctx.prices)
        return RunSummary(
            status=status,
            duration_seconds=round((utc_now() - self.manifest.created).total_seconds(), 3),
            usage=report.totals,
            tool_calls=report.tool_calls,
            estimated_cost_usd=report.estimated_cost_usd,
            unpriced_models=report.unpriced_models,
            budget_status=ctx.budget.status(),
        )

    def _finish(self, outcome: RunStatus, error: str | None) -> None:
        ctx = self.ctx
        ctx.processes.stop_all("the run ended")  # nothing the run started may keep running
        ctx.browsers.close_all("the run ended")
        close_backend(ctx.backend, "the run ended")  # containers the run still has
        ctx.board.flush()  # board.json and board.md as the run ended
        report = ctx.usage.report(ctx.prices)
        atomic_write_json(ctx.run_dir / "usage.json", report.model_dump(mode="json"))
        summary = self.summary(outcome)
        data = {"status": outcome, "summary": summary.model_dump(mode="json")}
        ctx.events.emit("run.finished", **data, **({"error": error} if error else {}))

        def record(manifest: RunManifest) -> None:
            manifest.summary = summary
            manifest.status = outcome

        self.store.update(ctx.run_id, record)
        self.hooks.on_finish(outcome, error, summary)  # never raises; its events are in the report
        from engineering_team.report import write_run_report  # reads what is now final

        write_run_report(ctx.run_dir)


def format_summary(summary: RunSummary) -> str:
    """The end-of-run report: exact token counts, a cost or "unknown", and budget standing."""

    usage = summary.usage
    lines = [
        f"Usage: {usage.total_tokens:,} tokens (prompt {usage.prompt_tokens:,}, of which "
        f"{usage.cached_prompt_tokens:,} cached; completion {usage.completion_tokens:,}) "
        f"in {usage.calls} model call(s) and {summary.tool_calls} tool call(s)"
    ]
    if summary.estimated_cost_usd is not None:
        lines.append(f"Estimated cost: ${summary.estimated_cost_usd:.4f}")
    else:
        unpriced = ", ".join(summary.unpriced_models) or "a model"
        lines.append(f"Estimated cost: unknown (no price for {unpriced}; see pricing in config)")
    budget = summary.budget_status
    if budget.state != "unlimited":
        detail = "; ".join(f"{limit.name} {limit.fraction:.0%}" for limit in budget.limits)
        lines.append(f"Budget: {budget.state} ({detail})")
        lines.extend(f"Budget note: {note}" for note in budget.notes)
        if budget.exceeded:
            lines.append(budget.exceeded)
    return "\n".join(lines)
