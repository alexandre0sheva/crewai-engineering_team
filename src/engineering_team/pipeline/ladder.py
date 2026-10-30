"""The ``auto`` strategy's escalation ladder: start cheap, escalate on the controller's evidence.

The rungs, cheapest first:

* ``solo``: the ``team`` strategy's lead with delegation disabled (``lead_recipe(alone=True)``),
  then the controller's verification and repair;
* ``team``: the lead with delegation enabled, its cap set by the task's size;
* ``pipeline``: the mode's full recipe (parallel packages, reviewers), mainly for large new
  projects;
* ``attempts``: best-of-N attempts, which T23 adds.

The starting rung comes from the task's size and risk (``ladder.*``; the size is the lean
profile's, decided before any model is called). When a rung's verification still fails after its
repair rounds, or its work stage could not deliver (the agent is stuck), the controller escalates
to the next rung **on the same workspace**, up to ``ladder.max_rung``, and never starts a rung
whose estimated cost (``bench/estimate.py``) is more than the budget has left. This module holds
the policy (``ladder_contracts.py`` the record, ``runs/<id>/ladder.json``); ``ladder_run.py``
walks it and
``ladder_brief.py`` writes what the next rung is told.

Each rung is one walk of the Flow with a fresh ``pipeline.json`` (the one before is kept as
``rungs/<rung>.json``). An escalated rung leaves out the controller skeleton the first rung ran
(profile, baseline, reproduction) and carries its facts over instead: the baseline must stay what
the checks said *before any change*, and a reproduction seen failing must not be written again.
The rung before's stage records are renamed ``<rung>:<stage>`` so resume never mistakes them for
the new rung's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engineering_team.bench.estimate import per_run_cost
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.lead import SKELETON_BEFORE, lead_recipe
from engineering_team.pipeline.lean import LEAN_MODES, LeanProfile
from engineering_team.pipeline.recipes import Recipe, RecipeError, StageSpec
from engineering_team.settings.sections import RungName

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import Settings

RUNGS_DIR = "rungs"  # each rung's final pipeline.json, as rungs/<rung>.json
RUNGS: tuple[RungName, ...] = ("solo", "team", "pipeline")  # T23 adds "attempts" on top
# The token priors (``bench/estimate.py``) a rung is estimated with, and the task scale per size.
ESTIMATE_AS: dict[str, str] = {"solo": "single", "team": "team", "pipeline": "pipeline"}
SIZE_SCALE: dict[str, float] = {"small": 1.0, "medium": 1.5, "large": 2.5}  # provisional (T33)
# The stop causes (``PipelineState.stopped_by``) that escalate: anything else (a budget stop, a
# cancel, a bug the team could not reproduce, a setup problem) ends the run where it is.
ESCALATE_ON = ("verification", "stage")
# Where a recipe the ladder climbs for comes from (``compose.COMPOSED_SOURCE`` is "composed").
LADDER_SOURCES = ("bundled", "composed")
# What an escalated rung keeps from the rung before instead of running the skeleton again.
CARRIED = ("isolation", "profile", "baseline", "repro", "fix", "coverage", "lean")


def _index(rung: str) -> int:
    return RUNGS.index(rung)


def lead_problem(base: Recipe) -> str:
    """Why the ladder does not climb for ``base`` (empty when it does): it then has one rung.

    It climbs for the bundled (or composed) recipes of the sized modes only: a maintenance task's
    recipe (grouped upgrades, a tests-only policy) and a recipe of your own run as written."""

    if base.mode not in LEAN_MODES:
        return f"the '{base.name}' recipe is not a new, feature or fix recipe"
    if base.source not in LADDER_SOURCES:
        return f"the '{base.name}' recipe is your own, so it runs as written"
    try:
        lead_recipe(base)
    except RecipeError as exc:
        return str(exc)
    return ""


def plan_ladder(settings: Settings, lean: LeanProfile, base: Recipe) -> LadderRecord:
    """Where the ladder starts and how high it may climb, with the reasons."""

    ladder = settings.ladder
    top = ladder.max_rung
    problem = lead_problem(base)
    if problem:
        return LadderRecord(
            start="pipeline",
            max_rung="pipeline",
            lowest="pipeline",
            reasons=[f"only the pipeline rung: {problem.split(' Use ')[0]}"],
        )
    start: RungName = {
        "small": ladder.start_small,
        "medium": ladder.start_medium,
        "large": ladder.start_large,
    }[lean.size]
    reasons = [f"{lean.size} task: ladder.start_{lean.size} = {start}"]
    if lean.high_risk and _index(start) < _index(ladder.start_high_risk):
        start = ladder.start_high_risk
        reasons.append(f"high risk: ladder.start_high_risk = {start}")
    if _index(start) > _index(top):
        start = top
        reasons.append(f"ladder.max_rung = {top}")
    return LadderRecord(start=start, max_rung=top, reasons=reasons)


def next_rung(rung: str, top: str) -> RungName | None:
    """The rung above ``rung``, or ``None`` at ``top``."""

    index = _index(rung) + 1
    return RUNGS[index] if index <= _index(top) else None


def rung_recipe(base: Recipe, rung: str, *, escalated: bool) -> Recipe:
    """The recipe a rung walks; an escalated rung's has no skeleton (deterministic, so a resumed
    rung gets the same digest)."""

    recipe = base if rung == "pipeline" else lead_recipe(base, alone=rung == "solo")
    return without_skeleton(recipe) if escalated else recipe


def without_skeleton(recipe: Recipe) -> Recipe:
    """``recipe`` without the controller and reproduce stages before its verify stage: their
    facts are carried over (``CARRIED``), so later stages read them without a producer."""

    position = next((i for i, s in enumerate(recipe.stages) if s.kind == "verify"), None)
    if position is None:
        return recipe
    kept: list[StageSpec] = []
    produced = {"request"}
    for index, stage in enumerate(recipe.stages):
        if index < position and stage.kind in SKELETON_BEFORE:
            continue
        inputs = [name for name in stage.inputs if name in produced]
        if inputs != stage.inputs:
            stage = stage.model_copy(update={"inputs": inputs})
        produced.update(stage.contract_outputs)
        kept.append(stage)
    if len(kept) == len(recipe.stages):
        return recipe
    try:
        copy = Recipe(
            name=recipe.name,
            description=recipe.description,
            mode=recipe.mode,
            policies=list(recipe.policies),
            stages=kept,
        )
    except ValueError as exc:
        raise RecipeError(f"Recipe '{recipe.name}' cannot be escalated to: {exc}") from exc
    copy.source, copy.path = recipe.source, recipe.path
    return copy


def carried_from(state: PipelineState) -> dict[str, Any]:
    """The skeleton's facts an escalated rung keeps (``CARRIED``) and the pinned checks'
    digests (a resumed session's bundle has none), as JSON."""

    data = state.model_dump(mode="json", include=set(CARRIED))
    carried = {name: value for name, value in data.items() if value is not None}
    pinned = state.verification
    carried["verification"] = {
        "checks_digest": pinned.checks_digest,
        "script_digests": dict(pinned.script_digests),
    }
    return carried


def estimate_usd(settings: Settings, rung: str, size: str) -> float | None:
    """What one walk of ``rung`` is estimated to cost (``None``: a model has no price)."""

    cost, _ = per_run_cost(settings, ESTIMATE_AS[rung], SIZE_SCALE.get(size, 1.0))
    return cost


def spent_usd(ctx: RunContext) -> float:
    """The run's known model cost so far."""

    return ctx.usage.report(ctx.settings.price_table()).known_cost_usd


def budget_left(ctx: RunContext) -> float | None:
    """What ``budget.max_cost_usd`` has left (``None``: no cost limit)."""

    limit = ctx.settings.budget.max_cost_usd
    return None if limit is None else limit - spent_usd(ctx)


def affordable(estimate: float | None, left: float | None) -> bool:
    return estimate is None or left is None or estimate <= left


def money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"
