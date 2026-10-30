"""Executing one stage of a recipe: board cards, retries, artifact checks, work packages.

``StageExecutor`` owns everything about *running* a stage; ``PipelineFlow`` decides *which*
stage runs next. Failures are exceptions: :class:`RunCancelled` and ``BudgetExceeded`` stop the
run (never retried), anything else is retried up to the stage's ``retry`` and then fails the
stage, which fails the run so ``resume`` can pick it up.
"""

from __future__ import annotations

from crewai.tools import BaseTool

from engineering_team.contracts import (
    Finding,
    VerificationRecord,
)
from engineering_team.modes import adopt as adopt  # noqa: F401  (registers the adopt actions)
from engineering_team.modes import maintain as maintain  # noqa: F401  (registers the actions)
from engineering_team.modes import review_mode as review_mode  # noqa: F401  (registers them too)
from engineering_team.modes.change_report import ChangeError, measure_change, render_noise
from engineering_team.modes.fix_contracts import Repro
from engineering_team.modes.fix_repro import run_reproduction
from engineering_team.modes.maintain_contracts import Upgrade
from engineering_team.modes.map_stage import run_map
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.modes.upgrade import run_upgrades
from engineering_team.pipeline.actions import CONTROLLER_ACTIONS
from engineering_team.pipeline.executor_base import MAX_NOTE_ERROR, MAX_SUMMARY
from engineering_team.pipeline.lead_exec import LeadExecutor
from engineering_team.pipeline.lean import reviewers
from engineering_team.pipeline.no_retry import NoRetry
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.resume import RESUME_NOTE, StagePlan
from engineering_team.pipeline.review import repair_brief
from engineering_team.pipeline.review_stage import run_review
from engineering_team.pipeline.spec_stage import (
    SpecError,
)
from engineering_team.pipeline.stages import (
    StageError,
    StageOutput,
    lead_teammate,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.events import stage_scope
from engineering_team.verification.loop import RepairCall, VerificationError, VerificationLoop


class StageExecutor(LeadExecutor):
    """Runs one stage of a recipe: kind-specific work, retries, and the final gate."""

    # -- the stage -----------------------------------------------------------------------

    def run(self, stage: StageSpec, entry: StagePlan) -> None:
        """Run ``stage`` to success or raise. Records it, moves its card, saves the state."""

        card = self.board.card_for(stage.name)
        self.board.start(card, lead_teammate(self.ctx, stage) if stage.teammates else None)
        try:
            with self.recorder.stage(stage.name):
                self._attempts(stage, entry)
                check_cancelled(self.ctx)  # an agent that stopped because of a cancel is not done
                self.board.verifying(card)
        except RunCancelled as exc:
            self.board.cancelled(card, str(exc))
            raise
        except KeyboardInterrupt:
            self.board.cancelled(card, "interrupted")
            raise
        except BaseException as exc:
            self.board.failed(card, f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._save()
        self.board.done(card)
        self.board.promote_next()
        self._save()
        self.checkpoints.stage(stage.name, self.state.summaries.get(stage.name, ""))

    def _attempts(self, stage: StageSpec, entry: StagePlan) -> None:
        note = entry.note or ""
        # A parallel stage retries per work package (see ``_packages``), not as a whole.
        for attempt in range(1 if stage.kind == "parallel" else 1 + stage.retry):
            check_cancelled(self.ctx)
            try:
                self._once(stage, entry, note)
                return
            except (RunCancelled, BudgetExceeded, NeedsInfo, NoRetry):
                raise
            except Exception as exc:
                if attempt >= stage.retry or stage.kind == "parallel":
                    raise
                reason = str(exc)[:MAX_NOTE_ERROR]
                self.ctx.events.emit(
                    "stage.retry", stage=stage.name, attempt=attempt + 1, error=reason
                )
                prefix = "" if isinstance(exc, SpecError) else f"{RESUME_NOTE} "
                note = f"{prefix}The previous attempt failed: {reason}"

    def _once(self, stage: StageSpec, entry: StagePlan, note: str) -> None:
        if stage.kind == "controller":
            action = CONTROLLER_ACTIONS.get(stage.action or "")
            if action is None:
                known = ", ".join(sorted(CONTROLLER_ACTIONS)) or "none registered"
                raise StageError(f"Unknown controller action {stage.action!r} (known: {known}).")
            self.state.summaries[stage.name] = action(self.ctx, self.state)[:MAX_SUMMARY]
        elif stage.kind == "parallel":
            self._packages(stage, entry, note)
        elif stage.kind == "verify":
            self._verify(stage)
        elif stage.kind == "review":
            self._review(stage)
        elif stage.kind == "analyze":
            self._analyze(stage)
        elif stage.kind == "reproduce":
            self._reproduce(stage)
        elif stage.kind == "upgrade":
            self._upgrade(stage)
        elif stage.kind == "lead":
            self._lead(stage, note)
        else:
            self._agent(stage, note)
        self._check_artifacts(stage)

    def _analyze(self, stage: StageSpec) -> None:
        """Analysts study the codebase in chunks, side by side; the controller writes the map."""

        teammate = lead_teammate(self.ctx, stage)

        def call(
            *,
            tools: list[BaseTool],
            lane: int | None,
            chunk: str = "",
            synthesis: str = "",
            profile: str = "",
        ) -> StageOutput:
            return self._call(
                stage,
                teammate,
                "",
                lane=lane,
                tools=tuple(tools),
                chunk=chunk,
                synthesis=synthesis,
                profile=profile,
            )

        summary = run_map(self.ctx, self.state, teammate, call)
        self.state.summaries[stage.name] = summary[:MAX_SUMMARY]

    def _reproduce(self, stage: StageSpec) -> None:
        """The debugger writes a failing reproduction; the controller runs it and requires red."""

        teammate = lead_teammate(self.ctx, stage)

        def attempt(note: str) -> Repro | None:
            output = self._call(stage, teammate, note)
            found = output.contracts.get("repro")
            return found if isinstance(found, Repro) else None

        self.state.summaries[stage.name] = run_reproduction(
            self.ctx, self.state, attempt, self._save
        )[:MAX_SUMMARY]
        self._save()

    def _verify(self, stage: StageSpec) -> None:
        """Run the controller's verification; its repair agent is the stage's first teammate.

        Raises ``VerificationError`` unless every required check passed on the current tree.
        """

        self.state.summaries[stage.name] = self._loop(stage).run()[:MAX_SUMMARY]

    def _loop(self, stage: StageSpec) -> VerificationLoop:
        teammate = lead_teammate(self.ctx, stage)

        def repair(call: RepairCall) -> str:
            output = self._call(stage, teammate, "", failures=call.failures, card_id=call.card_id)
            return output.summary

        return VerificationLoop(
            self.ctx,
            self.state,
            stage=stage.name,
            stage_card=self.board.card_for(stage.name),
            teammate=teammate,
            repair=repair,
            save=self._save,
            policies=self.recipe.policies,
        )

    def _review(self, stage: StageSpec) -> None:
        """Reviewers in parallel lanes; serious findings go to the repair agent of the verify
        stage (one round from the shared budget) and the project is verified again."""

        verify = next((s for s in self.recipe.stages if s.kind == "verify"), None)

        def call(teammate: str, tools: list[BaseTool], lane: int) -> StageOutput:
            return self._call(stage, teammate, "", lane=lane, tools=tuple(tools))

        def repair(findings: list[Finding]) -> str:
            assert verify is not None
            loop = self._loop(verify)

            def agent(call: RepairCall) -> str:
                return self._call(
                    verify, loop.teammate, "", findings=call.failures, card_id=call.card_id
                ).summary

            with stage_scope(verify.name):
                ran = loop.repair_findings(repair_brief(findings), [f.id for f in findings], agent)
                if ran is None:
                    return (
                        "No repair round was left (budget.max_repair_rounds), so these "
                        "findings were not fixed."
                    )
                try:
                    self._verify(verify)  # raises VerificationError if the project is not OK
                except VerificationError as exc:
                    loop.close_findings_repair(ran, f"not verified after the repair: {exc}"[:300])
                    raise
                loop.close_findings_repair(ran)
            return "Sent to repair and verified again; the findings were not reviewed again."

        kept = reviewers(self._lean, list(stage.teammates))
        if kept != stage.teammates:
            self.ctx.events.emit(
                "lean.reviewers",
                stage=stage.name,
                kept=kept,
                left_out=[key for key in stage.teammates if key not in kept],
                reason="small, low-risk task: one reviewer",
            )
        summary = run_review(
            self.ctx,
            self.state,
            stage.model_copy(update={"teammates": kept}),
            call,
            repair if verify and stage.repair else None,
            self._noise_section(),
        )
        self.state.summaries[stage.name] = summary[:MAX_SUMMARY]

    def _upgrade(self, stage: StageSpec) -> None:
        """Apply the planned upgrades in groups; the controller keeps what passes its checks."""

        teammate = lead_teammate(self.ctx, stage)

        def apply(group: list[Upgrade], number: int) -> None:
            text = "\n".join(
                f"- {u.package}: {u.current or '?'} -> {u.target} in {u.manifest or 'its manifest'}"
                + (f" ({u.reason})" if u.reason else "")
                for u in group
            )
            self._call(stage, teammate, "", upgrades=text, card_id=self.board.card_for(stage.name))

        self.state.summaries[stage.name] = run_upgrades(self.ctx, self.state, apply, self._save)[
            :MAX_SUMMARY
        ]

    def _noise_section(self) -> list[str]:
        """The diff-noise lines of a change to an existing project (nothing for a new one)."""

        if not self.state.isolation:
            return []
        try:
            measured = measure_change(self.ctx, self.state)
        except ChangeError:
            return []
        self.state.diff_noise = measured.noise
        self.ctx.events.emit(
            "review.diff_noise",
            total_lines=measured.noise.total_lines,
            outside_lines=measured.noise.outside_lines,
            outside_files=measured.noise.outside_files,
        )
        return ["## Diff noise", "", *render_noise(measured.noise)]

    def final_gate(self) -> None:
        """After the last stage: the project must still be what was verified.

        A later stage (release, documentation) may have edited the workspace after the verify
        stage; then the checks run again (and may be repaired, with the rounds that are left).
        With nothing changed the recorded results stand. The controller's report is rewritten
        either way, so an agent cannot leave its own words in ``docs/verification.md``.
        """

        stage = next((s for s in self.recipe.stages if s.kind == "verify"), None)
        if stage is None or self.state.verification.verdict is None:
            return  # the recipe does not verify, or its verify stage did not run
        with stage_scope(stage.name):
            self._verify(stage)
        self._save()


def reset_for(stage: StageSpec, entry: StagePlan, state: PipelineState) -> None:
    """Forget what a stage that runs again produced: its contracts, and (unless it continues
    where it stopped) its finished work packages."""

    if entry.action == "reuse":
        return
    for name in stage.contract_outputs:
        setattr(state, name, None)
    state.summaries.pop(stage.name, None)
    if stage.kind == "review":
        state.findings = []
    if stage.kind == "reproduce":  # a fresh reproduction starts from what the person gave
        state.fix, state.needs_info = None, []
    if stage.kind == "verify":  # a fresh verification gets its own repair rounds
        kept = state.verification
        state.checks = []
        state.verification = VerificationRecord(
            checks_digest=kept.checks_digest,
            script_digests=kept.script_digests,
            check_cards=kept.check_cards,
        )
    if stage.kind == "parallel" and entry.action != "continue":
        for package in state.packages.values():
            package.status, package.error = "pending", ""
