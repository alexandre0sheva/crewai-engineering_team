"""What every stage executor shares: its state, one agent call, and artifact checks."""

from __future__ import annotations

import threading
from dataclasses import replace

from crewai.tools import BaseTool

from engineering_team.artifacts import missing_artifacts
from engineering_team.contracts import (
    Spec,
    WorkPackage,
)
from engineering_team.modes import adopt as adopt  # noqa: F401  (registers the adopt actions)
from engineering_team.modes import maintain as maintain  # noqa: F401  (registers the actions)
from engineering_team.modes import review_mode as review_mode  # noqa: F401  (registers them too)
from engineering_team.modes.fix_repro import protected_paths
from engineering_team.modes.policies import SCOPES
from engineering_team.pipeline.board_sync import StageBoard
from engineering_team.pipeline.checkpoints import Checkpoints
from engineering_team.pipeline.packages import PlanError, plan_problems
from engineering_team.pipeline.recipes import Recipe, StageSpec
from engineering_team.pipeline.review import blocking, repair_brief
from engineering_team.pipeline.spec_stage import (
    Clarifier,
    SpecError,
    check_spec,
    render_spec,
)
from engineering_team.pipeline.stages import (
    StageError,
    StageOutput,
    StageRequest,
    StageRunner,
    lead_teammate,
    package_pool,
)
from engineering_team.pipeline.state import PipelineState, RunBundle
from engineering_team.runtime.cancel import check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.reports import SPEC
from engineering_team.runtime.session import RunRecorder
from engineering_team.tools import WriteScope

MAX_SUMMARY = 2000
MAX_NOTE_ERROR = 600


class ExecutorBase:
    """The run's state and the machinery that calls a stage's agent."""

    def __init__(
        self,
        ctx: RunContext,
        recipe: Recipe,
        state: PipelineState,
        bundle: RunBundle,
        runner: StageRunner,
        recorder: RunRecorder,
        board: StageBoard,
    ) -> None:
        self.ctx = ctx
        self.recipe = recipe
        self.state = state
        self.bundle = bundle
        self.runner = runner
        self.recorder = recorder
        self.board = board
        self._save_lock = threading.Lock()
        self.checkpoints = Checkpoints(ctx)
        self.clarifier = Clarifier(ctx)

    def _agent(self, stage: StageSpec, note: str) -> None:
        output = self._call(stage, lead_teammate(self.ctx, stage), note)
        if "spec" in output.contracts:
            output.contracts["spec"] = self._settle_spec(stage, note, output.contracts["spec"])
        for name, contract in output.contracts.items():
            setattr(self.state, name, contract)
        self.state.summaries[stage.name] = output.summary[:MAX_SUMMARY]
        if "plan" in output.contracts and self.state.plan is not None:
            host = next((s for s in self.recipe.stages if s.kind == "parallel"), None)
            shared = host is not None and host.allow_shared
            problems = plan_problems(self.state.plan, self.state.spec, allow_shared=shared)
            if problems:
                raise PlanError(
                    "The plan cannot be run: " + "; ".join(problems) + ". Fix the plan so that "
                    "every work package owns its own paths"
                    + (
                        ""
                        if shared
                        else " (never shared root files such as README.md or package.json)"
                    )
                    + " and delivers existing acceptance criteria."
                )
            if host is not None:
                self.board.ensure_package_cards(self.state.plan, host)

    def _settle_spec(self, stage: StageSpec, note: str, contract: object) -> Spec:
        """The analyst's spec, checked (``SpecError`` makes the stage retry once), with its
        blocking questions settled, and written to ``docs/spec.md`` by the controller."""

        if not isinstance(contract, Spec):
            raise SpecError("The stage returned no specification.")

        def revise(answers: str) -> Spec:
            again = self._call(stage, lead_teammate(self.ctx, stage), f"{note} {answers}".strip())
            revised = again.contracts.get("spec")
            if not isinstance(revised, Spec):
                raise SpecError("The analyst returned no specification.")
            return check_spec(revised)

        spec = self.clarifier.settle(check_spec(contract), revise)
        self.ctx.reports.write(SPEC, render_spec(spec))
        return spec

    def _call(
        self,
        stage: StageSpec,
        teammate: str,
        note: str,
        package: WorkPackage | None = None,
        *,
        lane: int | None = None,
        scope: WriteScope | None = None,
        failures: str = "",
        findings: str = "",
        tools: tuple[BaseTool, ...] | None = None,
        card_id: str | None = None,
        chunk: str = "",
        synthesis: str = "",
        profile: str = "",
        upgrades: str = "",
        audit: str = "",
    ) -> StageOutput:
        self.ctx.board.wait_while_paused(self.ctx.cancel_event)
        check_cancelled(self.ctx)
        if scope is None and stage.write_scope is not None:
            scope = WriteScope(allow=SCOPES[stage.write_scope])  # the recipe's lane for the stage
        if stage.kind != "reproduce" and (pinned := protected_paths(self.state)):
            # The reproduction was seen failing: no agent may edit it to make it pass.
            scope = WriteScope(
                allow=scope.allow if scope else ("**",),
                deny=(*(scope.deny if scope else ()), *pinned),
            )
        request = StageRequest(
            ctx=self.ctx,
            stage=stage,
            teammate=teammate,
            state=self.state.model_copy(deep=True),
            requirements=self.bundle.requirements,
            card_id=card_id
            or (
                self.state.packages[package.id].card_id
                if package
                else self.board.card_for(stage.name)
            ),
            steering=self.board.steering(teammate),
            note=note,
            package=package,
            lane=lane,
            write_scope=scope,
            failures=failures,
            findings=findings,
            tools=tools,
            roles=self._roles(),
            chunk=chunk,
            synthesis=synthesis,
            profile=profile,
            upgrades=upgrades,
            audit=audit or self._audit_text(),
        )
        if stage.prompt == "fix_findings" and not findings:  # a stage that fixes the audit's
            request = replace(
                request,
                findings=repair_brief(
                    blocking(self.state.findings, self.ctx.settings.review.fail_on)
                ),
            )
        output = self.runner.run(request)
        check_cancelled(self.ctx)
        return output

    def _audit_text(self) -> str:
        """The dependency audit's result, for a prompt (empty when none ran)."""

        from engineering_team.modes.maintain import audit_brief

        return audit_brief(self.state)

    def _roles(self) -> tuple[str, ...]:
        """The teammates the recipe's work packages can go to (the plan stage names them)."""

        host = next((s for s in self.recipe.stages if s.kind == "parallel"), None)
        return tuple(package_pool(host, self.ctx.team)) if host else ()

    def _check_artifacts(self, stage: StageSpec) -> None:
        if stage.verification_policy != "artifacts":
            return
        problems = missing_artifacts(self.ctx.workspace, *stage.file_outputs)
        if problems:
            raise StageError(
                "Required project artifacts are missing or incomplete: "
                + ", ".join(problems)
                + ". Create or complete them with the project filesystem tools."
            )

    def _save(self) -> None:
        """Write the state file; packages finish on different threads, so one at a time."""

        with self._save_lock:
            self.state.save(self.ctx.run_dir)
