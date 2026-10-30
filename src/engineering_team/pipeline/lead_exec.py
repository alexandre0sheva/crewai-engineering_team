"""Running the ``lead`` stage: the lead's session, the jobs it delegates, the checks between."""

from __future__ import annotations

from engineering_team.modes.fix_repro import protected_paths
from engineering_team.pipeline.delegation import Delegator
from engineering_team.pipeline.delegation_briefs import Job, delegation_cap
from engineering_team.pipeline.executor_base import MAX_SUMMARY
from engineering_team.pipeline.lead import lead_brief, resumed_note, security_signals
from engineering_team.pipeline.lead_contracts import LeadRecord
from engineering_team.pipeline.package_exec import PackageExecutor
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.stages import TEAM_GROUP, StageOutput, lead_teammate, stage_groups
from engineering_team.runtime.events import stage_scope
from engineering_team.runtime.snapshot import workspace_revision
from engineering_team.tools import WriteScope, build_tools
from engineering_team.verification.loop import VerificationError, VerificationLoop

READ_ONLY_KINDS = ("review", "security_review", "architect", "analyst")
MAX_CHECK_LINES = 12


class LeadExecutor(PackageExecutor):
    """Runs a ``lead`` stage (the ``team`` strategy; ``pipeline/lead.py``)."""

    def _lead(self, stage: StageSpec, note: str) -> None:
        ctx, state = self.ctx, self.state
        teammate = lead_teammate(ctx, stage)
        size = state.lean.size if state.lean is not None else None
        cap = delegation_cap(ctx, size)
        if state.lead is None:
            state.lead = LeadRecord(cap=cap)
        delegator = Delegator(
            ctx,
            state,
            self.board,
            lead=teammate,
            stage=stage.name,
            cap=cap,
            run_job=lambda job: self._delegated_job(stage, job),
            checks=self._interim_checks,
            save=self._save,
        )
        groups = stage_groups(ctx, teammate) + ((TEAM_GROUP,) if cap > 0 else ())
        pinned = protected_paths(state)  # a reproduction seen failing stays as it was
        scope = WriteScope(allow=("**",), deny=tuple(pinned)) if pinned else None
        tools = build_tools(ctx, groups=groups, write_scope=scope, agent=teammate)
        earlier = resumed_note(state)
        with ctx.delegation.opened(delegator):
            output = self._call(
                stage,
                teammate,
                f"{note}\n\n{earlier}".strip(),
                tools=tuple(tools),
                lead=lead_brief(ctx, state, teammate, cap),
            )
        state.summaries[stage.name] = output.summary[:MAX_SUMMARY]
        state.lead.signals = security_signals(ctx, self.bundle.requirements)
        state.lead.handed_back = workspace_revision(ctx.workspace)
        if state.lead.signals:
            ctx.events.emit(
                "lead.security_signals",
                signals=state.lead.signals,
                reviewed=state.lead.security_reviewed == state.lead.handed_back,
            )
        ctx.events.emit(
            "lead.handed_back",
            delegations=len(state.delegations),
            cap=cap,
            teammates=sorted({r.teammate for r in state.delegations}),
        )
        self._save()

    def _delegated_job(self, lead_stage: StageSpec, job: Job) -> StageOutput:
        """One delegated job as a small crew of its own (called on the job's lane thread)."""

        reading = job.kind in READ_ONLY_KINDS or not job.owned_paths
        spec = StageSpec(
            name=lead_stage.name,
            kind="review" if job.kind in ("review", "security_review") else "agent",
            teammates=[job.teammate],
            prompt=job.prompt,
        )
        tools = (
            tuple(
                build_tools(
                    self.ctx,
                    groups=stage_groups(self.ctx, job.teammate),
                    read_only=True,
                    agent=job.teammate,
                    lane=job.lane,
                )
            )
            if reading
            else None
        )
        return self._call(
            spec,
            job.teammate,
            "",
            lane=job.lane,
            scope=None if reading else WriteScope(allow=job.owned_paths),
            tools=tools,
            card_id=job.card_id,
            brief=job.brief,
            max_iter=job.max_iter,
        )

    def _interim_checks(self) -> str:
        """The verify stage's checks, run now without repair: what the lead is told after a job
        changed files (empty when the recipe does not verify)."""

        verify = next((s for s in self.recipe.stages if s.kind == "verify"), None)
        if verify is None:
            return ""
        loop = VerificationLoop(
            self.ctx,
            self.state,
            stage=verify.name,
            stage_card=self.board.card_for(verify.name),
            teammate=verify.teammates[0],
            repair=None,
            save=self._save,
            policies=self.recipe.policies,
        )
        with stage_scope(verify.name):
            try:
                verdict = loop.run()
            except VerificationError as exc:
                verdict = str(exc)
        lines = [
            f"- {check.id}: {check.status}" + (f" ({check.summary})" if check.summary else "")
            for check in self.state.checks[:MAX_CHECK_LINES]
        ]
        return "\n".join([verdict, *lines])
