"""Walking the escalation ladder (``ladder.py``): one Flow walk per rung, escalation between.

``run_ladder`` plans the ladder once (a resume reads ``ladder.json``), walks the current rung,
and after a walk that failed on the evidence (verification still failing, or a work stage that
could not deliver) escalates to the next rung on the same workspace: it keeps the rung's state as
``rungs/<rung>.json``, writes the next rung's brief, notes the move on the board card where the
rung stopped, and records ``ladder.escalated``. A rung is never started when its estimated cost is
more than the budget has left; the run then stops and says so.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import replace

from engineering_team.board.models import CONTROLLER
from engineering_team.board.store import BoardError
from engineering_team.contracts import RunManifest, RunStatus, utc_now
from engineering_team.pipeline.flow import run_flow
from engineering_team.pipeline.ladder import (
    ESCALATE_ON,
    RUNGS,
    RUNGS_DIR,
    affordable,
    budget_left,
    carried_from,
    estimate_usd,
    money,
    next_rung,
    plan_ladder,
    rung_recipe,
    spent_usd,
)
from engineering_team.pipeline.ladder_brief import escalation_brief, failing_checks, trace_entries
from engineering_team.pipeline.ladder_contracts import LadderRecord, RungRecord
from engineering_team.pipeline.lean import assess, count_files
from engineering_team.pipeline.recipes import Recipe
from engineering_team.pipeline.stages import StageRunner
from engineering_team.pipeline.state import STATE_FILENAME, PipelineState, RunBundle, RungSeed
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.run_store import RunStore

MAX_REASON = 300


def run_ladder(
    ctx: RunContext, base: Recipe, bundle: RunBundle, runner: StageRunner
) -> tuple[RunStatus, str]:
    """Climb the ladder for ``base`` (the mode's recipe) until a rung succeeds or it must stop."""

    record = LadderRecord.load(ctx.run_dir) if bundle.resume else None
    if record is None or record.current is None:  # also a run no rung fitted the budget of
        record = _plan(ctx, base, bundle)
        if record.current is None:
            return "failed", record.stopped
    while True:
        rung = record.current
        assert rung is not None
        record.stopped = ""
        recipe = current_recipe(record, base)
        saved = PipelineState.load(ctx.run_dir)
        again = (
            bundle.resume
            and saved is not None
            and saved.rung == rung.rung
            and saved.recipe_digest == recipe.digest
        )
        if not again and len(record.rungs) > 1:
            _set_aside(ctx, record, base)
        ctx.events.emit(
            "ladder.rung_started",
            rung=rung.rung,
            recipe=recipe.name,
            why=rung.why,
            estimate_usd=rung.estimate_usd,
            resumed=again,
        )
        seed = RungSeed(rung=rung.rung, carried=rung.carried, escalation=rung.escalation)
        pinned = rung.carried.get("verification") or {}
        walk = replace(
            bundle,
            resume=again,
            rung=seed,
            checks_digest=bundle.checks_digest or pinned.get("checks_digest", ""),
            script_digests=bundle.script_digests or pinned.get("script_digests", {}),
        )
        status, error = run_flow(ctx, recipe, walk, runner)
        state = PipelineState.load(ctx.run_dir) or PipelineState()
        _close(ctx, rung, status, state, error)
        if status == "succeeded":
            rung.status = "succeeded"
        reason = _escalation_reason(state, recipe) if status == "failed" else ""
        if not reason:
            record.save(ctx.run_dir)
            return status, error
        above = next_rung(rung.rung, record.max_rung)
        if above is None:
            why = (
                f"Not escalated: '{rung.rung}' is the highest rung allowed "
                f"(ladder.max_rung = {record.max_rung})."
            )
            return _stop(ctx, record, error, why)
        estimate, left = estimate_usd(ctx.settings, above, _size(rung)), budget_left(ctx)
        if not affordable(estimate, left):
            why = (
                f"Not escalated to '{above}': it is estimated at {money(estimate)}, more than the "
                f"{money(left)} left of budget.max_cost_usd."
            )
            return _stop(ctx, record, error, why)
        _escalate(ctx, record, base, state, above, reason, estimate)


def current_recipe(record: LadderRecord, base: Recipe) -> Recipe:
    """The recipe of the ladder's current rung (what a resume of the run must match)."""

    rung = record.current
    assert rung is not None
    return rung_recipe(base, rung.rung, escalated=len(record.rungs) > 1)


# -- planning ------------------------------------------------------------------------------


def _plan(ctx: RunContext, base: Recipe, bundle: RunBundle) -> LadderRecord:
    """The ladder of a fresh run, with its first rung: the planned start, or the highest rung
    below it the budget can pay for. Without one, ``stopped`` says why."""

    lean = assess(bundle.requirements, ctx.settings, count_files(ctx.workspace))
    ctx.events.emit("lean.profile", **lean.model_dump(exclude={"schema_version"}))
    record = plan_ladder(ctx.settings, lean, base)
    ctx.events.emit(
        "ladder.planned", start=record.start, max_rung=record.max_rung, reasons=record.reasons
    )
    left = budget_left(ctx)
    lowest = RUNGS.index(record.lowest)
    for rung in reversed(RUNGS[lowest : RUNGS.index(record.start) + 1]):
        estimate = estimate_usd(ctx.settings, rung, lean.size)
        if not affordable(estimate, left):
            continue
        why = "; ".join(record.reasons)
        if rung != record.start:
            why += (
                f"; '{record.start}' is estimated at "
                f"{money(estimate_usd(ctx.settings, record.start, lean.size))}, more than the "
                f"{money(left)} left of budget.max_cost_usd"
            )
        record.rungs.append(
            RungRecord(
                rung=rung,
                recipe=rung_recipe(base, rung, escalated=False).name,
                why=why,
                estimate_usd=estimate,
                spent_before_usd=spent_usd(ctx),
                trace_from=trace_entries(ctx),
                carried={"lean": lean.model_dump(mode="json")},
            )
        )
        break
    else:
        cheapest = RUNGS[lowest]
        record.stopped = (
            f"No rung fits the budget: '{cheapest}' is estimated at "
            f"{money(estimate_usd(ctx.settings, cheapest, lean.size))}, more than the "
            f"{money(left)} left of budget.max_cost_usd. Raise the budget to run this task."
        )
        ctx.events.emit("ladder.stopped", rung=None, reason=record.stopped)
    record.save(ctx.run_dir)
    return record


def _size(rung: RungRecord) -> str:
    lean = rung.carried.get("lean")
    return str(lean.get("size", "medium")) if isinstance(lean, dict) else "medium"


# -- after a walk --------------------------------------------------------------------------


def _close(
    ctx: RunContext, rung: RungRecord, status: RunStatus, state: PipelineState, error: str
) -> None:
    rung.finished = utc_now()
    rung.cost_usd = round(spent_usd(ctx) - rung.spent_before_usd, 6)
    rung.failing_checks = failing_checks(state)
    if status == "succeeded":
        rung.outcome = state.verification.verdict or "succeeded"
    else:
        rung.outcome = f"{status} ({state.stopped_by or 'error'}): {error}"[:MAX_REASON]


def _escalation_reason(state: PipelineState, recipe: Recipe) -> str:
    """Why the failed walk escalates, or ``""`` when it must not (the evidence is not the
    rung's: a budget stop, a cancel, a bug not reproduced, a setup problem, the skeleton)."""

    if state.stopped_by not in ESCALATE_ON:
        return ""
    if state.stopped_by == "verification":
        failing = failing_checks(state)
        count = f" ({len(failing)} required check{'s' if len(failing) != 1 else ''} failing)"
        return "verification still failed after the rung's repair rounds" + (
            count if failing else ""
        )
    stage = next((s for s in recipe.stages if s.name == state.stopped_at), None)
    if stage is None or stage.kind == "controller":
        return ""
    return f"the '{stage.name}' stage could not deliver (stuck): {state.error}"[:MAX_REASON]


def _stop(ctx: RunContext, record: LadderRecord, error: str, why: str) -> tuple[RunStatus, str]:
    record.stopped = why
    rung = record.current
    ctx.events.emit("ladder.stopped", rung=rung.rung if rung else None, reason=why)
    record.save(ctx.run_dir)
    return "failed", f"{error} {why}".strip()


def _escalate(
    ctx: RunContext,
    record: LadderRecord,
    base: Recipe,
    state: PipelineState,
    above: str,
    reason: str,
    estimate: float | None,
) -> None:
    rung = record.current
    assert rung is not None
    brief = escalation_brief(
        ctx, state, rung=rung.rung, to=above, reason=reason, since=rung.trace_from
    )
    _keep_state(ctx, rung.rung)
    card = state.stage_cards.get(state.stopped_at)
    if card is not None:  # a courtesy: the event and the ladder record are the record
        with contextlib.suppress(BoardError):
            ctx.board.comment(
                card, f"Escalated from '{rung.rung}' to '{above}': {reason}", author=CONTROLLER
            )
    rung.status = "escalated"
    record.rungs.append(
        RungRecord(
            rung=above,  # type: ignore[arg-type]
            recipe=rung_recipe(base, above, escalated=True).name,
            why=f"escalated from '{rung.rung}': {reason}",
            estimate_usd=estimate,
            spent_before_usd=spent_usd(ctx),
            trace_from=trace_entries(ctx),
            carried=carried_from(state),
            escalation=brief,
        )
    )
    record.save(ctx.run_dir)
    ctx.events.emit(
        "ladder.escalated",
        **{"from": rung.rung},
        to=above,
        reason=reason,
        failing_checks=rung.failing_checks,
        cost_usd=rung.cost_usd,
        estimate_usd=estimate,
    )


def _keep_state(ctx: RunContext, rung: str) -> None:
    """Keep the rung's final ``pipeline.json`` as ``rungs/<rung>.json`` (the report reads it)."""

    source = ctx.run_dir / STATE_FILENAME
    target = ctx.run_dir / RUNGS_DIR / f"{rung}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError:
        pass


def _set_aside(ctx: RunContext, record: LadderRecord, base: Recipe) -> None:
    """Rename the stage records of the rung before the current one to ``<rung>:<stage>`` so the
    new rung's resume never takes them for its own (safe to repeat)."""

    before = record.rungs[-2]
    names = {
        stage.name
        for stage in rung_recipe(base, before.rung, escalated=len(record.rungs) > 2).stages
    }

    def change(manifest: RunManifest) -> None:
        for stage in manifest.stages:
            if stage.name in names:
                stage.name = f"{before.rung}:{stage.name}"

    RunStore(ctx.workspace.root).update(ctx.run_id, change)
