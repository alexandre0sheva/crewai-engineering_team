"""Running a parallel stage: work packages scheduled by dependency and write scope."""

from __future__ import annotations

from engineering_team.contracts import (
    Plan,
    WorkPackage,
)
from engineering_team.modes import adopt as adopt  # noqa: F401  (registers the adopt actions)
from engineering_team.modes import maintain as maintain  # noqa: F401  (registers the actions)
from engineering_team.modes import review_mode as review_mode  # noqa: F401  (registers them too)
from engineering_team.pipeline.executor_base import MAX_NOTE_ERROR, MAX_SUMMARY, ExecutorBase
from engineering_team.pipeline.packages import SHARED_DENY
from engineering_team.pipeline.parallel import PackageOutcome, run_work_packages
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.resume import RESUME_NOTE, StagePlan
from engineering_team.pipeline.stages import (
    StageError,
    teammate_for,
)
from engineering_team.pipeline.state import PackageState
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.tools import WriteScope


class PackageExecutor(ExecutorBase):
    """Runs the work packages of a ``parallel`` stage."""

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
        deny = () if stage.allow_shared else SHARED_DENY
        scope = WriteScope(allow=tuple(package.owned_paths), deny=deny) if scoped else None
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
