"""The controller's side of delegation: the lead asks, the controller decides and runs.

The lead calls a teammate through a ``team`` tool (``tools/team_tools.py``); the call lands in
:meth:`Delegator.handle`. The controller then checks it (the lead's own session only, so depth 1;
the cap ``delegation.max_delegations``; a teammate the roster allows for this run; owned paths for
a job that writes, disjoint between parallel jobs), gives each job a board card and a lane
(``D1``, ``D2``, ...), runs the teammates as small crews of their own (side by side when there
are several), and hands back a compact typed result: what the controller saw (files changed,
commands run, its own checks after a job that changed files, what it cost) and, labelled as such,
what the teammate said. A teammate gets a brief, not the lead's history.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from engineering_team.contracts import ReviewReport
from engineering_team.pipeline.board_sync import StageBoard
from engineering_team.pipeline.delegation_briefs import (
    KIND_OF_TOOL,
    MAX_BRIEF,
    MAX_FILES,
    MAX_RESULT,
    PROMPTS,
    Brief,
    Job,
    JobRunner,
    brief_text,
    one_line,
    parse_briefs,
    render_result,
)
from engineering_team.pipeline.lead_contracts import DelegationKind, DelegationRecord
from engineering_team.pipeline.packages import paths_overlap
from engineering_team.pipeline.parallel import _fan_out
from engineering_team.pipeline.stages import StageOutput
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.delegation import LANE_PREFIX
from engineering_team.runtime.events import lane_scope
from engineering_team.runtime.snapshot import Snapshot, workspace_revision
from engineering_team.team.registry import WRITERS
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.support import ToolError

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext


class Delegator:
    """Checks and runs what the lead delegates; one per lead session."""

    def __init__(
        self,
        ctx: RunContext,
        state: PipelineState,
        board: StageBoard,
        *,
        lead: str,
        stage: str,
        cap: int,
        run_job: JobRunner,
        checks: Callable[[], str],
        save: Callable[[], None],
    ) -> None:
        self.ctx, self.state, self.board = ctx, state, board
        self.lead, self.stage, self.cap = lead, stage, cap
        self._run_job, self._checks, self._save = run_job, checks, save
        self._busy = threading.Lock()
        self._seconds: dict[str, float] = {}
        self._interrupted()

    @property
    def used(self) -> int:
        return len(self.state.delegations)

    def _interrupted(self) -> None:
        """Jobs an earlier session left running (the process stopped) are not running now."""

        for record in self.state.delegations:
            if record.status == "running":
                record.status, record.error = "interrupted", "the run stopped while it worked"
                self.board.cancelled(record.card_id, "interrupted: the run stopped")
        self._save()

    # -- what the tools call ----------------------------------------------------------------

    def handle(self, tool: str, arguments: dict[str, Any]) -> str:
        """Check and run one ``team`` tool call; a refusal is a ``ToolError`` saying what to do."""

        kind = KIND_OF_TOOL.get(tool)
        if kind is None:
            raise ToolError(f"Unknown team tool {tool!r}.")
        if not self._busy.acquire(blocking=False):
            raise ToolError("A delegation is running; teammates cannot delegate (depth 1).")
        try:
            briefs = parse_briefs(tool, kind, arguments, self.state)
            self._check(kind, briefs)
            return self._run(kind, briefs)
        finally:
            self._busy.release()

    def _check(self, kind: DelegationKind, briefs: list[Brief]) -> None:
        ctx = self.ctx
        if ctx.cancel_event.is_set():
            raise ToolError(
                f"{ctx.budget.tripped or 'The run was cancelled.'} Stop working and summarize."
            )
        if self.used + len(briefs) > self.cap:
            raise ToolError(
                f"Delegation limit reached: {self.used} of {self.cap} used, {len(briefs)} asked "
                "(delegation.max_delegations). Do the rest yourself."
            )
        for brief in briefs:
            if not ctx.team.usable(brief.teammate):
                callable_ = [m.key for m in ctx.team.enabled() if ctx.team.usable(m.key)]
                raise ToolError(
                    f"'{brief.teammate}' cannot be called in this run. Teammates you can call: "
                    f"{', '.join(callable_)}."
                )
            if kind == "delegate":
                if not brief.owned_paths:
                    raise ToolError("Give owned_paths: the globs this teammate may change.")
                if not set(ctx.team.get(brief.teammate).groups) >= WRITERS:
                    raise ToolError(f"'{brief.teammate}' cannot write files; ask it instead.")
        writers = [b for b in briefs if b.owned_paths]
        for index, first in enumerate(writers):
            for second in writers[index + 1 :]:
                if paths_overlap(first.owned_paths, second.owned_paths):
                    raise ToolError(
                        f"The owned paths of {first.teammate} ({', '.join(first.owned_paths)}) and "
                        f"{second.teammate} ({', '.join(second.owned_paths)}) may overlap; give "
                        "parallel jobs disjoint paths, or run them one after the other."
                    )

    # -- running ------------------------------------------------------------------------------

    def _run(self, kind: DelegationKind, briefs: list[Brief]) -> str:
        ctx = self.ctx
        jobs = [self._open(kind, brief) for brief in briefs]
        self._save()
        before = Snapshot.take(ctx.workspace)
        try:
            outcomes = _fan_out(
                ctx, jobs, lambda job, _: self._one(job), ctx.settings.parallel.max_parallel_agents
            )
        except (RunCancelled, BudgetExceeded) as exc:
            for job in jobs:
                self._stopped(job, str(exc))
            self._save()
            return f"ERROR: {exc} Stop working and summarize what is done."
        changed = _changed(before, Snapshot.take(ctx.workspace))
        checks = self._checks() if changed and any(job.owned_paths for job in jobs) else ""
        records = [
            self._close(job, outcome, changed, checks)
            for job, outcome in zip(jobs, outcomes, strict=True)
        ]
        self._save()
        return self._render(records, checks)

    def _open(self, kind: DelegationKind, brief: Brief) -> Job:
        """Record the job, give it a card in its lane, and say so."""

        ctx, number = self.ctx, len(self.state.delegations) + 1
        job_id = f"{LANE_PREFIX}{number}"
        limit = ctx.settings.delegation.max_iter
        steps = min(brief.max_steps, limit) if brief.max_steps > 0 else limit
        title = f"{job_id} {kind.replace('_', ' ')}: {' '.join(brief.brief.split())}"[:200]
        why = f"\n\nWhy (the lead): {brief.why}" if brief.why else ""
        card = ctx.board.create_card(
            title,
            kind="delegation",
            status="ready",
            assignee=brief.teammate,
            stage=self.stage,
            parent_id=self.board.card_for(self.stage),
            description=f"{brief.brief}{why}"[:MAX_BRIEF],
            criteria_ids=list(brief.criteria),
            owned_paths=list(brief.owned_paths),
            lane=job_id,
        )
        self.state.delegations.append(
            DelegationRecord(
                id=job_id,
                kind=kind,
                teammate=brief.teammate,
                brief=brief.brief,
                why=brief.why,
                owned_paths=list(brief.owned_paths),
                criteria=list(brief.criteria),
                card_id=card.id,
            )
        )
        self.board.start(card.id, brief.teammate, lane=job_id)
        ctx.events.emit(
            "delegation.started",
            id=job_id,
            kind=kind,
            teammate=brief.teammate,
            why=brief.why,
            owned_paths=list(brief.owned_paths),
            max_steps=steps,
        )
        return Job(
            id=job_id,
            kind=kind,
            teammate=brief.teammate,
            prompt=PROMPTS[kind],
            brief=brief_text(kind, brief),
            owned_paths=tuple(brief.owned_paths),
            max_iter=steps,
            card_id=card.id,
        )

    def _record(self, job: Job) -> DelegationRecord:
        return next(r for r in self.state.delegations if r.id == job.id)

    def _measure(self, record: DelegationRecord) -> None:
        used = self.ctx.delegation.usage(record.id)
        record.calls, record.tokens = used.calls, used.total_tokens
        record.cost_usd = used.cost(self.ctx.prices)
        record.commands = used.commands
        record.seconds = round(self._seconds.get(record.id, 0.0), 3)

    def _close(
        self,
        job: Job,
        outcome: StageOutput | Exception,
        changed: Sequence[str],
        checks: str,
    ) -> DelegationRecord:
        record = self._record(job)
        self._measure(record)
        scope = WriteScope(allow=job.owned_paths)
        record.files_changed = [p for p in changed if job.owned_paths and scope.permits(p)]
        record.checks = checks if record.files_changed else ""
        if isinstance(outcome, Exception):
            record.status, record.error = "failed", f"{type(outcome).__name__}: {outcome}"[:600]
            self.board.failed(job.card_id, record.error)
        else:
            record.status, record.summary = "succeeded", outcome.summary[:2000]
            report = outcome.contracts.get("review")
            if isinstance(report, ReviewReport):
                record.findings = list(report.findings)
                record.summary = report.summary[:2000] or record.summary
            if job.kind == "security_review" and self.state.lead is not None:
                self.state.lead.security_reviewed = workspace_revision(self.ctx.workspace)
            self.board.verifying(job.card_id)
            self.board.done(job.card_id, one_line(record))
        self._comment(record)
        self.ctx.events.emit(
            "delegation.finished",
            id=record.id,
            kind=record.kind,
            teammate=record.teammate,
            status=record.status,
            why=record.why,
            files=record.files_changed[:MAX_FILES],
            findings=len(record.findings),
            calls=record.calls,
            tokens=record.tokens,
            cost_usd=record.cost_usd,
            error=record.error,
        )
        return record

    def _stopped(self, job: Job, reason: str) -> None:
        record = self._record(job)
        self._measure(record)
        record.status, record.error = "cancelled", reason[:600]
        self.board.cancelled(job.card_id, reason)
        self.ctx.events.emit(
            "delegation.finished",
            id=record.id,
            kind=record.kind,
            teammate=record.teammate,
            status="cancelled",
            why=record.why,
            cost_usd=record.cost_usd,
            error=record.error,
        )

    def _comment(self, record: DelegationRecord) -> None:
        if record.card_id is None:
            return
        try:
            self.ctx.board.comment(record.card_id, one_line(record), author="controller")
        except Exception:  # the board is a view; the state keeps the record
            self.ctx.events.emit("pipeline.board_warning", card_id=record.card_id)

    def _render(self, records: list[DelegationRecord], checks: str) -> str:
        share = max(400, (MAX_RESULT - 600) // max(len(records), 1))
        parts = [render_result(record)[:share] for record in records]
        if checks:
            parts.append(f"The controller's checks after this: {checks}"[:600])
        parts.append(f"Delegations used: {self.used} of {self.cap}.")
        return "\n\n".join(parts)[:MAX_RESULT]

    def _one(self, job: Job) -> StageOutput:
        """One job in its own lane (called on a lane thread)."""

        ctx, started = self.ctx, time.monotonic()
        with lane_scope(job.lane):
            ctx.events.emit("lane.started", kind="delegation", unit=f"{job.id} {job.teammate}")
            try:
                output = self._run_job(job)
            except (RunCancelled, BudgetExceeded):
                ctx.events.emit(
                    "lane.finished", unit=f"{job.id} {job.teammate}", status="cancelled"
                )
                raise
            except Exception as exc:
                self._seconds[job.id] = time.monotonic() - started
                ctx.events.emit(
                    "lane.finished",
                    unit=f"{job.id} {job.teammate}",
                    status="failed",
                    error=str(exc)[:300],
                )
                raise
            self._seconds[job.id] = time.monotonic() - started
            ctx.events.emit("lane.finished", unit=f"{job.id} {job.teammate}", status="succeeded")
            return output


def _changed(before: Snapshot, after: Snapshot) -> list[str]:
    paths = set(before.files) | set(after.files)
    return sorted(
        path
        for path in paths
        if (before.files.get(path) or None) is None
        or (after.files.get(path) or None) is None
        or before.files[path].sha256 != after.files[path].sha256
    )
