"""The pipeline as a CrewAI Flow: a typed state, and one router that walks the recipe.

``PipelineFlow`` is ``Flow[PipelineState]``. ``begin`` (``@start``) loads or creates the state,
creates the board's stage cards and, when resuming, decides which stages are finished.
``advance`` (``@router``) runs the next stage that is not finished and returns ``"next_stage"``
to run the next one, or ``"finished"``, ``"failed"`` or ``"cancelled"``; the listeners record
the end. Walking a recipe from one router (rather than one hard-coded method per stage) keeps
recipes data: a new mode is a new YAML file. Each stage builds its own small crew
(``pipeline/stages.py``); the Flow only owns order, state, and where the run stopped.
"""

from __future__ import annotations

from crewai.flow.flow import Flow, listen, or_, router, start
from pydantic import PrivateAttr

from engineering_team.contracts import RunStatus, StageRecord
from engineering_team.pipeline.board_sync import StageBoard
from engineering_team.pipeline.executor import StageExecutor, reset_for
from engineering_team.pipeline.recipes import CONDITIONS, Recipe
from engineering_team.pipeline.resume import ResumeError, StagePlan, plan_resume
from engineering_team.pipeline.stages import StageRunner
from engineering_team.pipeline.state import PipelineState, RunBundle, request_hash
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.session import RunRecorder
from engineering_team.runtime.snapshot import workspace_revision

MAX_ERROR = 600


class PipelineFlow(Flow[PipelineState]):
    """Walks a recipe's stages in order; see the module docstring."""

    _ctx: RunContext = PrivateAttr()
    _recipe: Recipe = PrivateAttr()
    _bundle: RunBundle = PrivateAttr()
    _runner: StageRunner = PrivateAttr()
    _recorder: RunRecorder = PrivateAttr()
    _board: StageBoard = PrivateAttr()
    _executor: StageExecutor = PrivateAttr()
    _plans: dict[str, StagePlan] = PrivateAttr(default_factory=dict)
    _index: int = PrivateAttr(default=0)
    _outcome: RunStatus = PrivateAttr(default="failed")
    _error: str = PrivateAttr(default="")

    @classmethod
    def create(
        cls, ctx: RunContext, recipe: Recipe, bundle: RunBundle, runner: StageRunner
    ) -> PipelineFlow:
        flow = cls(suppress_flow_events=True)
        flow._ctx, flow._recipe, flow._bundle, flow._runner = ctx, recipe, bundle, runner
        flow._recorder = RunRecorder.attach(ctx)
        return flow

    @property
    def outcome(self) -> tuple[RunStatus, str]:
        """How the walk ended: ``succeeded``, ``failed`` or ``cancelled``, and the error text."""

        return self._outcome, self._error

    # -- the flow ------------------------------------------------------------------------

    @start()
    def begin(self) -> None:
        ctx, recipe, state = self._ctx, self._recipe, self.state
        digest, requested = recipe.digest, request_hash(self._bundle.requirements)
        loaded = PipelineState.load(ctx.run_dir) if self._bundle.resume else None
        if loaded is not None:
            if loaded.recipe_digest != digest:
                raise ResumeError(
                    f"Recipe '{recipe.name}' changed since this run started; resuming would mix "
                    "two plans. Start a new run instead."
                )
            for name in PipelineState.model_fields:
                setattr(state, name, getattr(loaded, name))
        state.recipe, state.recipe_digest, state.request_hash = recipe.name, digest, requested
        if loaded is None:  # a fresh run pins the user's checks; a resumed one keeps its own
            state.verification.checks_digest = self._bundle.checks_digest
            state.verification.script_digests = dict(self._bundle.script_digests)
        self._board = StageBoard(ctx, recipe, state)
        self._executor = StageExecutor(
            ctx, recipe, state, self._bundle, self._runner, self._recorder, self._board
        )
        self._executor.checkpoints.start()
        if self._bundle.resume:
            ctx.board.unpause()  # a pause belonged to the session that was stopped
        self._plans = self._decide(loaded is not None)
        self._board.ensure_stage_cards()
        if state.plan is not None:
            host = next((s for s in recipe.stages if s.kind == "parallel"), None)
            if host is not None:
                self._board.ensure_package_cards(state.plan, host)
        state.save(ctx.run_dir)
        ctx.events.emit("pipeline.started", recipe=recipe.name, resume=self._bundle.resume)

    @router(or_(begin, "next_stage"))
    def advance(self) -> str:
        recipe = self._recipe
        if self._index >= len(recipe.stages):
            return self._finish_gate()
        stage = recipe.stages[self._index]
        self._index += 1
        entry = self._plans.get(stage.name, StagePlan("run", "not started"))
        try:
            self._ctx.board.wait_while_paused(self._ctx.cancel_event)
            check_cancelled(self._ctx)
            if entry.action == "reuse":
                self._ctx.events.emit("stage.reused", stage=stage.name, reason=entry.reason)
                return "next_stage"
            settings = self._ctx.settings
            reason = next(
                (name for name in stage.skip_if if CONDITIONS[name](self.state, settings)), None
            )
            if (
                reason is None
                and stage.optional
                and not any(self._ctx.team.usable(key) for key in stage.teammates)
            ):
                reason = "no_enabled_teammate"
            if reason is not None:
                self._skip(stage.name, reason)
                return "next_stage"
            self._executor.run(stage, entry)
            return "next_stage"
        except RunCancelled as exc:
            return self._halt("cancelled", str(exc))
        except BudgetExceeded as exc:
            return self._halt("failed", str(exc))
        except Exception as exc:  # the stage already recorded itself as failed
            return self._halt("failed", f"Stage '{stage.name}' failed: {exc}")

    def _finish_gate(self) -> str:
        """Every stage is done; the project must still be what the verify stage verified."""

        recipe = self._recipe
        try:
            self._ctx.board.wait_while_paused(self._ctx.cancel_event)
            check_cancelled(self._ctx)
            self._executor.final_gate()
            state = self.state
            title = state.spec.title if state.spec else state.triage.title if state.triage else ""
            self._executor.checkpoints.final(f"{recipe.name}: {title}" if title else "")
            return "finished"
        except RunCancelled as exc:
            return self._halt("cancelled", str(exc))
        except BudgetExceeded as exc:
            return self._halt("failed", str(exc))
        except Exception as exc:
            return self._halt("failed", f"Final verification: {exc}")

    @listen("finished")
    def finish(self) -> None:
        self._outcome = "succeeded"
        self._ctx.events.emit("pipeline.finished", status="succeeded")

    @listen(or_("failed", "cancelled"))
    def stop(self) -> None:
        self._ctx.events.emit("pipeline.finished", status=self._outcome, error=self._error)

    # -- helpers -------------------------------------------------------------------------

    def _halt(self, status: RunStatus, error: str) -> str:
        self._outcome, self._error = status, error[:MAX_ERROR]
        self.state.error = self._error
        self.state.save(self._ctx.run_dir)
        return status

    def _skip(self, stage: str, condition: str) -> None:
        self._recorder.skip_stage(stage, f"skipped: {condition}")
        self._board.cancelled(self._board.card_for(stage), f"skipped: {condition}")
        self._board.promote_next()

    def _decide(self, resuming: bool) -> dict[str, StagePlan]:
        """The action per stage: everything runs on a fresh run; a resume keeps what is finished."""

        recipe, ctx, state = self._recipe, self._ctx, self.state
        if not resuming:
            return {stage.name: StagePlan("run", "fresh run") for stage in recipe.stages}
        records = self._recorder.manifest.stages
        plans = plan_resume(recipe, records, state, workspace_revision(ctx.workspace))
        previous = {record.name: record for record in records}
        for stage in recipe.stages:
            entry = plans[stage.name]
            reset_for(stage, entry, state)
            if entry.action != "reuse" and stage.name in previous:
                old = previous[stage.name]
                self._recorder.store.record_stage(
                    ctx.run_id, StageRecord(name=stage.name, attempts=old.attempts)
                )
        ctx.events.emit(
            "resume.plan",
            stages={name: {"action": e.action, "reason": e.reason} for name, e in plans.items()},
        )
        return plans


def run_flow(
    ctx: RunContext, recipe: Recipe, bundle: RunBundle, runner: StageRunner
) -> tuple[RunStatus, str]:
    """Walk ``recipe`` once and return how it ended: ``(status, error)``."""

    flow = PipelineFlow.create(ctx, recipe, bundle, runner)
    flow.kickoff()
    return flow.outcome


__all__ = ["PipelineFlow", "run_flow"]
