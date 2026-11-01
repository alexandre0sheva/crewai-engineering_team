"""``bench run --dry-run``: what a batch would cost, from the price table, before spending.

The token counts are *priors*, not measurements: a typical run's prompt and completion tokens per
strategy at task scale 1.0 (see ``scale`` in ``task.yaml``). They are deliberately generous, and the
answer is a range. Task 34 replaces them with measured values; until then treat the figure as an
order of magnitude and set ``--budget-usd`` as the hard cap.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from engineering_team.bench.plan import RunSpec
from engineering_team.model_routing import TIER_NAMES
from engineering_team.pricing import estimate_cost
from engineering_team.settings import Settings

# (prompt tokens, completion tokens) of one run at scale 1.0. Agent loops re-send the context,
# so prompt tokens dominate.
TOKEN_PRIORS: dict[str, tuple[int, int]] = {
    "pipeline": (1_200_000, 150_000),
    "single": (500_000, 70_000),
    "team": (800_000, 100_000),  # a lead and a few delegated jobs (the escalation ladder's rung)
}
# Which share of a run's tokens each model tier handles.
TIER_MIX: dict[str, dict[str, float]] = {
    "pipeline": {"lead": 0.25, "reviewer": 0.15, "worker": 0.60},
    "single": {"worker": 1.0},
    "team": {"lead": 0.10, "reviewer": 0.10, "worker": 0.80},
}
LOW_FACTOR, HIGH_FACTOR = 0.5, 2.0
# The prior each rung of the escalation ladder is estimated with (``pipeline/ladder.py``; the
# ``attempts`` rung: ``attempts.ladder_count`` walks of the ``team`` prior, and the judge).
RUNG_PRIORS: dict[str, str] = {
    "solo": "single",
    "team": "team",
    "pipeline": "pipeline",
    "attempts": "team",
}
LADDER_MODES = ("new", "feature", "fix")  # pipeline/lean.py LEAN_MODES: where the ladder climbs


@dataclass
class EstimateRow:
    task: str
    strategy: str
    runs: int
    per_run_usd: float | None


@dataclass
class BatchEstimate:
    rows: list[EstimateRow] = field(default_factory=list)
    unpriced_models: list[str] = field(default_factory=list)

    @property
    def total_usd(self) -> float | None:
        if not self.rows or any(row.per_run_usd is None for row in self.rows):
            return None
        return sum(row.per_run_usd * row.runs for row in self.rows if row.per_run_usd is not None)

    @property
    def runs(self) -> int:
        return sum(row.runs for row in self.rows)

    @property
    def range_usd(self) -> tuple[float, float] | None:
        total = self.total_usd
        return None if total is None else (total * LOW_FACTOR, total * HIGH_FACTOR)


def per_run_cost(settings: Settings, strategy: str, scale: float) -> tuple[float | None, set[str]]:
    """The estimated cost of one run (``None`` if a model on the path has no price) and the
    models without a price."""

    prompt, completion = TOKEN_PRIORS[strategy]
    prices = settings.price_table()
    total: float | None = 0.0
    unpriced: set[str] = set()
    for tier, share in TIER_MIX[strategy].items():
        model = settings.resolve_model("bench-estimate", tier=tier).model
        cost = estimate_cost(
            prices.lookup(model),
            prompt_tokens=round(prompt * scale * share),
            completion_tokens=round(completion * scale * share),
        )
        if cost is None:
            unpriced.add(model)
            total = None
        elif total is not None:
            total += cost
    return total, unpriced


def priced_as(settings: Settings, strategy: str, mode: str, size: str) -> str:
    """The prior a run of ``strategy`` is estimated with. ``auto`` is priced as the rung it starts
    on for the task's ``size`` (``ladder.start_*``; a maintenance task or a review always runs on
    the ``pipeline`` rung). An escalation is not priced: ``--run-budget-usd`` bounds it."""

    if strategy != "auto":
        return strategy
    if mode not in LADDER_MODES:
        return "pipeline"
    ladder = settings.ladder
    start = {"large": ladder.start_large, "medium": ladder.start_medium}.get(
        size, ladder.start_small
    )
    return RUNG_PRIORS[min(start, ladder.max_rung, key=list(RUNG_PRIORS).index)]


def unpriced_models(settings: Settings) -> list[str]:
    """Models any teammate could use under these settings that have no price.

    The team's cost cap can only stop a run whose spend it can compute, so a live batch must not
    start with one of these.
    """

    prices = settings.price_table()
    found = {
        settings.resolve_model(role, tier=tier).model
        for role in ("engineering_lead", "worker")
        for tier in TIER_NAMES
    }
    return sorted(model for model in found if prices.lookup(model) is None)


def estimate_batch(specs: Sequence[RunSpec], settings: Settings) -> BatchEstimate:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    scales: dict[tuple[str, str], float] = {}
    priors: dict[tuple[str, str], str] = {}
    for spec in specs:
        key = (spec.task.id, spec.strategy)
        counts[key] += 1
        scales[key] = spec.task.scale
        priors[key] = priced_as(settings, spec.strategy, spec.task.mode, spec.task.size)
    result = BatchEstimate()
    unpriced: set[str] = set()
    for (task, strategy), runs in sorted(counts.items()):
        cost, missing = per_run_cost(settings, priors[(task, strategy)], scales[(task, strategy)])
        unpriced |= missing
        result.rows.append(EstimateRow(task, strategy, runs, cost))
    result.unpriced_models = sorted(unpriced)
    return result
