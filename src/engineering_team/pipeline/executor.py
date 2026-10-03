"""Executing one stage of a recipe: board cards, retries, artifact checks, work packages.

``StageExecutor`` owns everything about *running* a stage; ``PipelineFlow`` decides *which*
stage runs next. Failures are exceptions: :class:`RunCancelled` and ``BudgetExceeded`` stop the
run (never retried), anything else is retried up to the stage's ``retry`` and then fails the
stage, which fails the run so ``resume`` can pick it up.
"""

from __future__ import annotations

import threading

from crewai.tools import BaseTool

from engineering_team.artifacts import missing_artifacts
from engineering_team.contracts import Finding, Plan, Spec, VerificationRecord, WorkPackage
from engineering_team.modes import adopt as adopt  # noqa: F401  (registers the adopt actions)
from engineering_team.modes.map_stage import run_map
from engineering_team.pipeline.actions import CONTROLLER_ACTIONS
from engineering_team.pipeline.board_sync import StageBoard
from engineering_team.pipeline.checkpoints import Checkpoints
from engineering_team.pipeline.packages import SHARED_DENY, PlanError, plan_problems
from engineering_team.pipeline.parallel import PackageOutcome, run_work_packages
from engineering_team.pipeline.recipes import Recipe, StageSpec
from engineering_team.pipeline.resume import RESUME_NOTE, StagePlan
from engineering_team.pipeline.review import repair_brief
from engineering_team.pipeline.review_stage import run_review
from engineering_team.pipeline.spec_stage import (
    SPEC_FILE,
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
    teammate_for,
)
from engineering_team.pipeline.state import PackageState, PipelineState, RunBundle
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import stage_scope
from engineering_team.runtime.session import RunRecorder
from engineering_team.tools import WriteScope
from engineering_team.verification.loop import RepairCall, VerificationError, VerificationLoop

MAX_SUMMARY = 2000
MAX_NOTE_ERROR = 600


class StageExecutor:
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
            except (RunCancelled, BudgetExceeded):
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

        summary = run_review(self.ctx, self.state, stage, call, repair if verify else None)
        self.state.summaries[stage.name] = summary[:MAX_SUMMARY]

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

    def _agent(self, stage: StageSpec, note: str) -> None:
        output = self._call(stage, lead_teammate(self.ctx, stage), note)
        if "spec" in output.contracts:
            output.contracts["spec"] = self._settle_spec(stage, note, output.contracts["spec"])
        for name, contract in output.contracts.items():
            setattr(self.state, name, contract)
        self.state.summaries[stage.name] = output.summary[:MAX_SUMMARY]
        if "plan" in output.contracts and self.state.plan is not None:
            problems = plan_problems(self.state.plan, self.state.spec)
            if problems:
                raise PlanError(
                    "The plan cannot be run: " + "; ".join(problems) + ". Fix the plan so that "
                    "every work package owns its own paths (never shared root files such as "
                    "README.md or package.json) and delivers existing acceptance criteria."
                )
            host = next((s for s in self.recipe.stages if s.kind == "parallel"), None)
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
        self.ctx.workspace.write_file(SPEC_FILE, render_spec(spec))
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
    ) -> StageOutput:
        self.ctx.board.wait_while_paused(self.ctx.cancel_event)
        check_cancelled(self.ctx)
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
        )
        output = self.runner.run(request)
        check_cancelled(self.ctx)
        return output

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

    # -- work packages -------------------------------------------------------------------

    def _packages(self, stage: StageSpec, entry: StagePlan, note: str) -> None:
        plan = self.state.plan
        if plan is None:
            raise StageError(f"Stage '{stage.name}' needs the plan, which no earlier stage made.")
        self.board.ensure_package_cards(plan, stage)
        for package in plan.work_packages:
            self.state.packages.setdefault(package.id, PackageState())
        limit = self.ctx.settings.parallel.max_parallel_agents
        done = {pid for pid, known in self.state.packages.items() if known.status == "succeeded"}

        def job(package: WorkPackage, lane: int, retry_note: str) -> str:
            return self._package(stage, package, lane, retry_note or note, scoped=limit > 1)

        # ``retry`` of a parallel stage is per package, inside the engine, so a stage-level
        # retry never reruns packages that already finished.
        outcomes = run_work_packages(self.ctx, plan, job, limit, done=done, retry=stage.retry)
        self._settle(plan, outcomes)

    def _package(
        self, stage: StageSpec, package: WorkPackage, lane: int, note: str, *, scoped: bool
    ) -> str:
        """One attempt at one work package, in ``lane`` (called on a worker thread)."""

        teammate = teammate_for(stage, package, self.ctx.team)
        known = self.state.packages[package.id]
        if known.status in ("running", "failed"):  # an earlier attempt, maybe in another session
            if RESUME_NOTE not in note:
                note = f"{RESUME_NOTE} {note}".strip()
            if known.error and "previous attempt failed" not in note:
                note += f" The previous attempt failed: {known.error}"
        known.status, known.error = "running", ""
        known.attempts += 1
        self.board.start(known.card_id, teammate, lane=lane)
        self._save()
        # With one lane nothing runs beside the agent, so it keeps the whole workspace.
        scope = WriteScope(allow=tuple(package.owned_paths), deny=SHARED_DENY) if scoped else None
        try:
            output = self._call(stage, teammate, note, package, lane=lane, scope=scope)
        except RunCancelled:
            self.board.cancelled(known.card_id, "cancelled")
            raise
        except BaseException as exc:
            known.status, known.error = "failed", str(exc)[:MAX_NOTE_ERROR]
            self.board.failed(known.card_id, known.error)
            self._save()
            raise
        known.status, known.summary = "succeeded", output.summary[:MAX_SUMMARY]
        self.board.verifying(known.card_id)
        self.board.done(known.card_id)
        self._save()
        return known.summary

    def _settle(self, plan: Plan, outcomes: list[PackageOutcome]) -> None:
        """Record packages that were skipped, then fail the stage if a required one is missing."""

        by_id = {package.id: package for package in plan.work_packages}
        for outcome in outcomes:
            known = self.state.packages[outcome.id]
            if outcome.status == "skipped":
                known.status, known.error = "skipped", outcome.error
                self.board.cancelled(known.card_id, f"skipped: {outcome.error}")
            elif outcome.status == "failed":
                known.status, known.error = "failed", outcome.error
        self._save()
        missing = [o for o in outcomes if not o.ok and by_id[o.id].required]
        optional = [o.id for o in outcomes if not o.ok and not by_id[o.id].required]
        if optional:
            self.ctx.events.emit("parallel.optional_missing", packages=optional)
        if missing:
            detail = "; ".join(f"{o.id} ({o.status}: {o.error or 'no detail'})" for o in missing)
            raise StageError(
                f"Required work package(s) did not succeed: {detail}. Finished packages are "
                "kept; fix the cause and run `engineering-team resume`."
            )

    def _save(self) -> None:
        """Write the state file; packages finish on different threads, so one at a time."""

        with self._save_lock:
            self.state.save(self.ctx.run_dir)


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
