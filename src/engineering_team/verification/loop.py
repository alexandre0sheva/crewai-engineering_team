"""The verify stage: run the checks, repair what failed (bounded), run them again.

The loop is controller code. It decides what runs (``profiles``), runs it (``Verifier``),
judges the results (``verdict``), and only hands *failures* to an agent. The agent's reply is
never read as evidence: after each repair round the controller runs the checks again, and the
verdict follows those results alone. Rounds are bounded by ``budget.max_repair_rounds`` (shared
by the whole run) and by the run's other budgets. Calling it again on an unchanged workspace
reuses the recorded results, so a final check after later stages costs nothing unless a stage
edited the project.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.contracts import CheckResult, CheckSpec
from engineering_team.modes.fix_repro import fix_checks, record_green, tampered
from engineering_team.modes.policy_checks import POLICY_PREFIX, policy_results
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.reports import QA_NOTES, VERIFICATION
from engineering_team.verification.baseline_compare import apply_baseline
from engineering_team.verification.cards import CheckCards
from engineering_team.verification.checks_file import ChecksFileError, pinned_checks
from engineering_team.verification.criteria import map_criteria
from engineering_team.verification.profiles import build_checks
from engineering_team.verification.report import render_report
from engineering_team.verification.revision import verification_revision
from engineering_team.verification.verdict import Judgement, failures_for_repair, judge
from engineering_team.verification.verifier import Verifier

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

MAX_NOTE = 4000
MAX_ERROR = 300


class VerificationError(RuntimeError):
    """The project is not verified (``failed`` or ``partial``); the message says why."""


@dataclass(frozen=True)
class FindingsRepair:
    """A repair round for review findings that ran: its card, and whether it changed the project."""

    card_id: str | None
    changed: bool


@dataclass(frozen=True)
class RepairCall:
    """What the repair agent is told, and where its card is."""

    round: int
    failures: str
    card_id: str | None


# Runs one repair attempt with an agent and returns what it said (never taken as evidence).
RepairAgent = Callable[[RepairCall], str]


class VerificationLoop:
    def __init__(
        self,
        ctx: RunContext,
        state: PipelineState,
        *,
        stage: str,
        stage_card: str | None,
        teammate: str,
        repair: RepairAgent | None,
        save: Callable[[], None],
        policies: Sequence[str] = (),
    ) -> None:
        self.ctx = ctx
        self.state = state
        self.record = state.verification
        self.stage = stage
        self.teammate = teammate
        self.repair = repair
        self.save = save
        self.cards = CheckCards(ctx, self.record.check_cards, stage=stage, parent=stage_card)
        self.verifier = Verifier(ctx, cards=self.cards, script_digests=self.record.script_digests)
        self.max_rounds = ctx.settings.budget.max_repair_rounds
        self.policies = list(policies)

    # -- the loop --------------------------------------------------------------------------

    def run(self) -> str:
        """Verify (repairing while allowed). Returns a one-line summary when the project is
        ``verified``; raises :class:`VerificationError` otherwise. The report is written either
        way."""

        ctx, record = self.ctx, self.record
        ctx.events.emit("verify.started", rounds_used=record.rounds)
        try:
            user = pinned_checks(ctx, record.checks_digest)
        except ChecksFileError as exc:
            return self._conclude([], [], Judgement("failed", [str(exc)]))
        # A change to an existing project is checked with what the project itself uses and
        # judged against its baseline; the plan's own commands do not replace them.
        planned = build_checks(
            ctx,
            None if self.state.baseline else self.state.plan,
            user,
            extra=fix_checks(ctx, self.state),
        )
        record.notes = planned.notes
        checks = planned.checks

        results = self._recorded(checks) or self._verify(checks)
        while self._repairable(results):
            results = self._repair(checks, results)
        return self._conclude(checks, results, self._judge(results))

    def _judge(self, results: list[CheckResult]) -> Judgement:
        """The verdict; a reproduction edited after it was seen failing proves nothing."""

        judgement = judge(results, verification_revision(self.ctx.workspace))
        changed = tampered(self.ctx, self.state)
        if not changed:
            return judgement
        problem = (
            f"The reproduction file(s) {', '.join(changed)} changed after they were seen failing, "
            "so a pass proves nothing. Restore them (or start a new run)."
        )
        return Judgement("failed", [problem, *judgement.problems])

    def _recorded(self, checks: list[CheckSpec]) -> list[CheckResult] | None:
        """The results already recorded, if they are for this very workspace and these checks."""

        state, record = self.state, self.record
        same = {c.id for c in checks} == {
            r.id for r in state.checks if not r.id.startswith(POLICY_PREFIX)
        }
        if state.checks and same and record.revision == verification_revision(self.ctx.workspace):
            self.ctx.events.emit("verify.reused", revision=record.revision)
            return list(state.checks)
        return None

    def _verify(self, checks: list[CheckSpec], number: int | None = None) -> list[CheckResult]:
        results = self.verifier.run(checks, round=self._next_file() if number is None else number)
        results = [*results, *policy_results(self.ctx, self.state, self.policies)]
        if self.state.baseline is not None:
            results = apply_baseline(
                results, {check.id: check.cwd for check in checks}, self.state.baseline
            )
        self.state.checks = results
        self.record.revision = results[0].revision if results else None
        self.save()
        return results

    def _next_file(self) -> int:
        directory = self.ctx.run_dir / "verification"
        return len(list(directory.glob("round-*.json"))) if directory.is_dir() else 0

    def _repairable(self, results: list[CheckResult]) -> bool:
        failing = any(r.required and r.status == "failed" for r in results)
        return (
            failing and self.repair is not None and self.ctx.budget.may_repair(self.record.rounds)
        )

    def _repair(self, checks: list[CheckSpec], results: list[CheckResult]) -> list[CheckResult]:
        ctx, record = self.ctx, self.record
        check_cancelled(ctx)
        ctx.budget.check()  # a run over budget stops here rather than spend more on repairs
        assert self.repair is not None
        record.rounds += 1
        number = record.rounds
        failing = [r.id for r in results if r.required and r.status == "failed"]
        card = self.cards.open_repair(number, self.teammate, failing)
        before = verification_revision(ctx.workspace)
        brief = f"Repair round {number} of {self.max_rounds}.\n\n{failures_for_repair(results)}"
        ctx.events.emit("verify.repair", round=number, checks=failing)
        error = ""
        try:
            summary = self.repair(RepairCall(number, brief, card))
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # a crashed repair attempt is a used round, not the end
            summary, error = "", f"{type(exc).__name__}: {exc}"[:MAX_ERROR]
        check_cancelled(ctx)
        self._note(number, summary, error)
        changed = verification_revision(ctx.workspace) != before
        fresh = self._verify(checks) if changed else results
        still = [r.id for r in fresh if r.id in failing and r.status != "passed"]
        fixed = [i for i in failing if i not in still]
        self.cards.close_repair(card, fixed, still)
        record.repair_log.append(self._log_line(number, failing, still, changed, error))
        self.save()
        return fresh

    def repair_findings(
        self, brief: str, ids: list[str], agent: RepairAgent
    ) -> FindingsRepair | None:
        """One repair round for review findings, from the same budget as repairs for failing checks.

        ``agent`` is told ``brief``; the caller verifies again afterwards (``run``), which is what
        decides the outcome. Returns ``None`` when no round is left; otherwise the caller closes
        the round's card with :meth:`close_findings_repair` once it has verified again.
        """

        ctx, record = self.ctx, self.record
        if not ctx.budget.may_repair(record.rounds):
            ctx.events.emit("verify.repair_skipped", findings=ids, rounds=record.rounds)
            return None
        check_cancelled(ctx)
        ctx.budget.check()
        record.rounds += 1
        number = record.rounds
        card = self.cards.open_repair(number, self.teammate, ids)
        before = verification_revision(ctx.workspace)
        ctx.events.emit("verify.repair", round=number, findings=ids)
        error = ""
        try:
            summary = agent(
                RepairCall(number, f"Repair round {number} of {self.max_rounds}.\n\n{brief}", card)
            )
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # a crashed repair attempt is a used round, not the end
            summary, error = "", f"{type(exc).__name__}: {exc}"[:MAX_ERROR]
        check_cancelled(ctx)
        self._note(number, summary, error)
        changed = verification_revision(ctx.workspace) != before
        extra = " The repair agent failed to run." if error else ""
        extra += "" if changed else " It changed nothing in the project."
        record.repair_log.append(
            f"Round {number}: sent review finding(s) {', '.join(ids)} to the repair agent.{extra}"
        )
        self.save()
        return FindingsRepair(card, changed)

    def close_findings_repair(self, repair: FindingsRepair, error: str = "") -> None:
        """Close the card of a findings repair after verifying again: done when the project
        changed and the checks passed (those are the evidence), failed otherwise."""

        passed = [r.id for r in self.state.checks if r.status == "passed"]
        reason = error or ("" if repair.changed else "the repair changed nothing")
        self.cards.close_findings_repair(repair.card_id, evidence=passed, reason=reason)

    @staticmethod
    def _log_line(
        number: int, failing: list[str], still: list[str], changed: bool, error: str
    ) -> str:
        outcome = f"still failing: {', '.join(still)}" if still else "all of them pass now"
        extra = " The repair agent failed to run." if error else ""
        extra += "" if changed else " It changed nothing in the project."
        return f"Round {number}: sent {', '.join(failing)} to the repair agent; {outcome}.{extra}"

    def _note(self, number: int, summary: str, error: str) -> None:
        """Keep what the agent said in ``docs/qa-notes.md``, labelled as unverified narrative."""

        reports = self.ctx.reports
        try:
            existing = reports.read(QA_NOTES)
        except OSError:
            existing = "# QA notes\n\nWhat the repair agent said, for people. Not evidence: the "
            existing += f"controller's checks are in `{reports.label(VERIFICATION)}`.\n"
        said = summary.strip()[:MAX_NOTE] or (f"(no summary; {error})" if error else "(no summary)")
        reports.write(QA_NOTES, f"{existing.rstrip()}\n\n## Repair round {number}\n\n{said}\n")

    # -- the end ---------------------------------------------------------------------------

    def _conclude(
        self, checks: list[CheckSpec], results: list[CheckResult], judgement: Judgement
    ) -> str:
        ctx, record = self.ctx, self.record
        record.verdict = judgement.verdict
        record.problems = judgement.problems
        record_green(ctx, self.state, results)
        record.coverage = map_criteria(ctx.workspace, self.state.spec, checks, results)
        text = render_report(
            record,
            results,
            judgement,
            max_rounds=self.max_rounds,
            notes=ctx.reports.label(QA_NOTES),
        )
        ctx.reports.write(VERIFICATION, text)
        self.save()
        ctx.events.emit(
            "verify.verdict",
            verdict=judgement.verdict,
            rounds=record.rounds,
            problems=judgement.problems,
            checks={r.id: r.status for r in results},
        )
        required = [r for r in results if r.required]
        if judgement.verdict == "verified":
            return (
                f"Verified: {len(required)} required check(s) passed ({len(results)} ran), "
                f"{record.rounds} repair round(s)."
            )
        raise VerificationError(
            f"Not verified ({judgement.verdict}): {'; '.join(judgement.problems)}. "
            f"See {ctx.reports.label(VERIFICATION)}."
        )
