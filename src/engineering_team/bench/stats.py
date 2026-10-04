"""The statistics a benchmark report needs: a Wilson interval and cost per success."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

Z_95 = 1.959963984540054  # the two-sided 95 % normal quantile


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for a pass rate (95 % by default).

    Unlike the plain normal interval it stays inside [0, 1] and is honest for the small samples a
    benchmark has: 3 of 3 passes is not "100 % ± 0". With no trials nothing is known: (0, 1).
    """

    if trials < 0 or not 0 <= successes <= trials:
        raise ValueError(f"Need 0 <= successes <= trials, got {successes} of {trials}.")
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def total_cost(costs: Iterable[float | None]) -> float | None:
    """The sum of the costs, or ``None`` when any is unknown (an unknown cost is never $0)."""

    items = list(costs)
    if any(cost is None for cost in items):
        return None
    return float(sum(cost for cost in items if cost is not None))


def cost_per_success(costs: Iterable[float | None], successes: int) -> float | None:
    """What one *successful* task cost: all spend, failed runs included, over the successes.

    ``None`` when nothing succeeded or any cost is unknown.
    """

    spent = total_cost(costs)
    if spent is None or successes <= 0:
        return None
    return spent / successes
