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
from engineering_team.contracts import RunManifest, RunStatus, RunSummary, StageRecord, utc_now
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import stage_scope
from engineering_team.runtime.run_store import RunStore


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
    run's cancel event is set; ``interrupted`` for Ctrl-C; ``failed`` otherwise).
    """

    def __init__(self, ctx: RunContext, store: RunStore) -> None:
        self.ctx = ctx
        self.store = store

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
        settings_payload = ctx.settings.model_dump(mode="json")
        canonical = json.dumps(settings_payload, sort_keys=True, default=str)
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

    @property
    def manifest(self) -> RunManifest:
        return self.store.load(self.ctx.run_id)

    @contextlib.contextmanager
    def running(self) -> Iterator[RunRecorder]:
        ctx = self.ctx
        self.store.set_status(ctx.run_id, "running")
        ctx.events.emit("run.started", mode=self.manifest.mode, project=ctx.settings.project_name)
        outcome: RunStatus = "succeeded"
        error: str | None = None
        try:
            with bind_run(ctx.run_id, ctx.events):
                yield self
            flush_bridge()  # deliver CrewAI's in-flight events, then the last safe point:
            ctx.budget.check()  # raises BudgetExceeded if the run overspent
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
        manifest, and a budget check at both boundaries."""

        ctx = self.ctx
        ctx.budget.check()
        attempts = next((s.attempts for s in self.manifest.stages if s.name == name), 0) + 1
        record = StageRecord(name=name, status="running", started=utc_now(), attempts=attempts)
        self.store.record_stage(ctx.run_id, record)
        ctx.events.emit("stage.started", stage=name, attempt=attempts)
        status: str = "succeeded"
        try:
            with stage_scope(name):
                yield
            flush_bridge()
            ctx.budget.check()
        except KeyboardInterrupt:
            status = "interrupted"
            raise
        except BaseException:
            status = "failed"
            raise
        finally:
            ctx.processes.stop_stage(name)  # a server started in this stage does not outlive it
            ctx.browsers.stop_stage(name)  # nor does a browser context opened in it
            record = record.model_copy(update={"status": status, "finished": utc_now()})
            self.store.record_stage(ctx.run_id, record)
            ctx.events.emit("stage.finished", stage=name, status=status)

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
