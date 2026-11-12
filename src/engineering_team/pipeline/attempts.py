"""Best-of-N attempts: N independent attempts, verified by the controller, one chosen blind.

``--attempts N`` (``attempts.count``) runs every run as N attempts of the recipe its strategy
would walk (``auto``: the rung it would start on); the ladder's ``attempts`` rung
(``ladder.max_rung = "attempts"``) does the same with ``attempts.ladder_count`` attempts of the
``team`` rung when the ``pipeline`` rung's verification still fails. Before anything starts, the
attempts and the judge are estimated (``bench/estimate.py``) and refused when they would cost
more than the budget has left.

The attempts run in parallel (at most ``parallel.max_parallel_agents`` at a time), each a run of
its own in a copy of the workspace (``attempts_run.py``, ``attempts_tree.py``), varied by
``attempts.vary``. Each is verified by the controller in its own run; only the attempts that
passed enter selection (``select.py``: a blind judge, or the only one that passed). When none
passed, the one with the most passing required checks is kept in the workspace and the run
fails, saying so: it is never reported as a verified result.

The chosen attempt's changes are copied into the run's workspace (and committed as the
``attempts`` stage on the run's branch), its final state becomes the run's, and the controller
checks that the run's tree is then exactly the tree the attempt was verified on. Every attempt
is kept as ``attempts/<label>.patch`` with its run under ``attempts/<label>/run``; the copies
are deleted. ``attempts.json`` records it all, and is the attempts' cost ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from engineering_team.bench.estimate import per_run_cost
from engineering_team.contracts import RunStatus
from engineering_team.git.port import GitError
from engineering_team.modes.change_report import ChangeError
from engineering_team.pipeline.attempts_contracts import AttemptRecord, AttemptsRecord, Selection
from engineering_team.pipeline.attempts_run import (
    ATTEMPTS_STAGE,
    Attempt,
    CancelLink,
    adopt_state,
    attempt_recipe,
    keep,
    run_one,
    variant,
    write_change_summary,
)
from engineering_team.pipeline.attempts_tree import (
    AttemptCopyError,
    apply_changes,
    check_size,
    copies_root,
    remove_copies,
)
from engineering_team.pipeline.checkpoints import Checkpoints
from engineering_team.pipeline.ladder import SIZE_SCALE, affordable, budget_left, money, spent_usd
from engineering_team.pipeline.lean import assess, count_files
from engineering_team.pipeline.parallel import _fan_out
from engineering_team.pipeline.recipes import Recipe
from engineering_team.pipeline.select import (
    MAX_DIFF_CHARS,
    MAX_REQUEST_CHARS,
    Candidate,
    LlmFactory,
    judge_estimate,
    select,
)
from engineering_team.pipeline.stages import StageRunner
from engineering_team.pipeline.state import RunBundle
from engineering_team.runtime.bridge import flush_bridge
from engineering_team.runtime.cancel import check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import RunRecorder
from engineering_team.runtime.snapshot import Snapshot, workspace_revision
from engineering_team.settings import Settings

RunnerFactory = Callable[[], StageRunner]


class AttemptsFailed(Exception):
    """The attempts ended without a verified result (the message says why)."""


def wants_attempts(settings: Settings) -> bool:
    return settings.attempts.count > 1


def estimate_attempts(settings: Settings, kind: str, size: str, count: int) -> float | None:
    """``count`` runs of the ``kind`` prior (``bench/estimate.py``) and the judge's call at its
    largest (``None``: a model has no price)."""

    each, _ = per_run_cost(settings, kind, SIZE_SCALE.get(size, 1.0))
    judge = judge_estimate(settings, count * MAX_DIFF_CHARS + MAX_REQUEST_CHARS)
    if each is None or judge is None:
        return None
    return round(each * count + judge, 6)


def run_attempts(
    ctx: RunContext,
    recipe: Recipe,
    bundle: RunBundle,
    runner_factory: RunnerFactory,
    *,
    count: int,
    kind: str,
    strategy: str,
    source: str = "option",
    llm_factory: LlmFactory | None = None,
) -> tuple[RunStatus, str]:
    """Run ``count`` attempts of ``recipe`` and keep the chosen one; returns ``(status, error)``.
    ``kind`` is the estimate prior of one attempt; a cancel or a budget stop raises."""

    settings = ctx.settings
    record = AttemptsRecord(
        count=count,
        vary=settings.attempts.vary,
        source=source,  # type: ignore[arg-type]
        recipe=recipe.name,
    )
    size = assess(bundle.requirements, settings, count_files(ctx.workspace)).size
    record.estimate_usd = estimate_attempts(settings, kind, size, count)
    left = budget_left(ctx)
    if not affordable(record.estimate_usd, left):
        return _stop(
            ctx,
            record,
            f"Not started: {count} attempts and the judge are estimated at "
            f"{money(record.estimate_usd)}, more than the {money(left)} left of "
            "budget.max_cost_usd. Lower --attempts or raise the budget.",
        )
    try:
        check_size(ctx.workspace, count)
    except AttemptCopyError as exc:
        return _stop(ctx, record, str(exc))
    try:
        with RunRecorder.attach(ctx).stage(ATTEMPTS_STAGE):
            _attempts(ctx, record, recipe, bundle, runner_factory, strategy, llm_factory)
    except AttemptsFailed as exc:
        return "failed", str(exc)
    return "succeeded", ""


def _stop(ctx: RunContext, record: AttemptsRecord, why: str) -> tuple[RunStatus, str]:
    record.stopped = why
    record.selection = Selection(method="stopped", reason=why)
    record.save(ctx.run_dir)
    ctx.events.emit("attempts.stopped", reason=why, estimate_usd=record.estimate_usd)
    return "failed", why


def _attempts(
    ctx: RunContext,
    record: AttemptsRecord,
    recipe: Recipe,
    bundle: RunBundle,
    runner_factory: RunnerFactory,
    strategy: str,
    llm_factory: LlmFactory | None,
) -> None:
    count = record.count
    Checkpoints(ctx).start()  # a new project becomes a repository first: every copy has one
    start = Snapshot.take(ctx.workspace)
    left = budget_left(ctx)
    reserve = judge_estimate(ctx.settings, count * MAX_DIFF_CHARS + MAX_REQUEST_CHARS) or 0.0
    share = None if left is None else (left - reserve) / count
    attempts: list[Attempt] = []
    for number in range(1, count + 1):
        settings, harness, described = variant(ctx, number, share)
        entry = AttemptRecord(label=f"a{number}", run_id=f"{ctx.run_id}-a{number}", vary=described)
        record.attempts.append(entry)
        attempts.append(Attempt(entry, settings, harness))
    record.save(ctx.run_dir)
    ctx.events.emit(
        "attempts.started",
        count=count,
        vary=record.vary,
        source=record.source,
        recipe=record.recipe,
        estimate_usd=record.estimate_usd,
        attempts=[{"label": a.label, "vary": a.vary} for a in record.attempts],
    )
    mode = RunStore(ctx.workspace.root).load(ctx.run_id).mode
    walked = attempt_recipe(recipe)
    root = copies_root(ctx.workspace, ctx.run_id)
    before = spent_usd(ctx)
    try:
        with CancelLink(ctx.cancel_event) as link:

            def work(attempt: Attempt, _lane: int) -> Attempt:
                return run_one(
                    ctx, attempt, walked, bundle, runner_factory(),
                    start=start, copy_to=root / attempt.record.label, mode=mode,
                    strategy=strategy, link=link,
                )  # fmt: skip

            results = _fan_out(ctx, attempts, work, ctx.settings.parallel.max_parallel_agents)
        for attempt, result in zip(attempts, results, strict=True):  # in label order
            if isinstance(result, Exception):  # the controller's own bookkeeping failed
                attempt.record.status = "failed"
                attempt.record.outcome = f"error: {type(result).__name__}: {result}"[:300]
            keep(ctx, attempt)
            _emit_finished(ctx, attempt.record)
        record.save(ctx.run_dir)
        ctx.budget.check()  # the run's budget stopped the attempts: the run fails here
        check_cancelled(ctx)
        _choose(ctx, record, attempts, bundle.requirements, llm_factory)
    finally:
        flush_bridge()
        record.spent_usd = round(spent_usd(ctx) - before, 6)
        record.save(ctx.run_dir)
        remove_copies(ctx.workspace, ctx.run_id)


def _emit_finished(ctx: RunContext, entry: AttemptRecord) -> None:
    ctx.events.emit(
        "attempts.finished",
        label=entry.label,
        status=entry.status,
        verdict=entry.verdict,
        checks_passed=entry.checks_passed,
        checks_total=entry.checks_total,
        diff_lines=entry.diff_lines,
        cost_usd=entry.cost_usd,
    )


def _choose(
    ctx: RunContext,
    record: AttemptsRecord,
    attempts: Sequence[Attempt],
    request: str,
    llm_factory: LlmFactory | None,
) -> None:
    """Select, then make the chosen attempt the run's result; raises :class:`AttemptsFailed`
    when no attempt passed (the closest is still kept, and said to be unverified)."""

    passing = [a for a in attempts if a.record.passed]
    if passing:
        selection = select(ctx, request, [_candidate(a) for a in passing], llm_factory=llm_factory)
    else:
        closest = min(
            attempts,
            key=lambda a: (-a.record.checks_passed, a.record.diff_lines, a.record.label),
        )
        entry = closest.record
        selection = Selection(
            method="none-passed",
            chosen=entry.label,
            reason=(
                f"No attempt passed verification. Attempt {entry.label} came closest "
                f"({entry.checks_passed} of {entry.checks_total} required checks passed); its "
                "changes are in the workspace for you to inspect, but they are not a verified "
                "result."
            ),
        )
    record.selection = selection
    chosen = next(a for a in attempts if a.record.label == selection.chosen)
    if chosen.workspace is not None:
        apply_changes(chosen.diff, chosen.workspace, ctx.workspace)
        selection.revision_match = workspace_revision(ctx.workspace) == chosen.record.revision
    adopt_state(ctx, chosen)
    Checkpoints(ctx).stage(
        ATTEMPTS_STAGE, f"best of {record.count}: {chosen.record.label} ({selection.method})"
    )
    record.save(ctx.run_dir)
    ctx.events.emit(
        "attempts.selected",
        method=selection.method,
        chosen=selection.chosen,
        reason=selection.reason,
        anonymised=selection.anonymised,
        revision_match=selection.revision_match,
        judge_error=selection.judge_error or None,
    )
    if not passing:
        raise AttemptsFailed(selection.reason)
    if selection.revision_match is not False:
        try:  # the patch and summary of a change to an existing project, for the chosen attempt
            write_change_summary(ctx)
        except (ChangeError, GitError) as exc:
            raise AttemptsFailed(
                f"The chosen attempt's change could not be measured: {exc}"
            ) from exc
    if selection.revision_match is False:
        raise AttemptsFailed(
            f"Attempt {chosen.record.label} passed in its copy, but the run's workspace does not "
            "hold the same tree after its changes were applied, so its verification does not "
            "carry over. Its patch is attempts/"
            f"{chosen.record.label}.patch in the run directory."
        )


def _candidate(attempt: Attempt) -> Candidate:
    entry = attempt.record
    checks = (
        f"{entry.checks_passed} of {entry.checks_total} required checks passed"
        if entry.checks_total
        else "no checks ran; the attempt's run succeeded"
    )
    state = attempt.state
    findings: list[Any] = list(state.findings) if state is not None else []
    return Candidate(
        label=entry.label,
        diff=attempt.diff.patch,
        diff_lines=entry.diff_lines,
        files=entry.files_changed,
        checks=checks,
        findings=[f"{f.severity}: {f.summary}" for f in findings],
    )
