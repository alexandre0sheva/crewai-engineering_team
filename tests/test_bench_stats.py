"""The statistics a benchmark report rests on: the Wilson interval and cost per success."""

from __future__ import annotations

import pytest

from engineering_team.bench.stats import (
    cost_per_success,
    mean,
    median,
    total_cost,
    wilson_interval,
)


def test_wilson_interval_matches_known_values() -> None:
    low, high = wilson_interval(8, 10)

    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_all_passes_still_leave_a_wide_interval() -> None:
    low, high = wilson_interval(3, 3)

    assert low == pytest.approx(0.4385, abs=1e-4)
    assert high == pytest.approx(1.0)


def test_no_passes_are_not_a_certain_zero() -> None:
    low, high = wilson_interval(0, 10)

    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-4)


def test_no_trials_know_nothing() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_the_interval_narrows_as_the_sample_grows() -> None:
    small = wilson_interval(5, 10)
    large = wilson_interval(50, 100)

    assert (large[1] - large[0]) < (small[1] - small[0])
    assert small[0] < 0.5 < small[1]
    assert large[0] < 0.5 < large[1]


@pytest.mark.parametrize(("successes", "trials"), [(-1, 3), (4, 3), (0, -1)])
def test_impossible_counts_are_refused(successes: int, trials: int) -> None:
    with pytest.raises(ValueError):
        wilson_interval(successes, trials)


def test_cost_per_success_charges_the_failures_to_the_successes() -> None:
    assert cost_per_success([1.0, 2.0, 3.0], successes=2) == pytest.approx(3.0)


def test_cost_per_success_is_unknown_without_a_success() -> None:
    assert cost_per_success([1.0, 2.0], successes=0) is None


def test_an_unknown_cost_is_never_counted_as_zero() -> None:
    assert total_cost([1.0, None]) is None
    assert cost_per_success([1.0, None], successes=1) is None
    assert total_cost([]) == 0.0


def test_median_and_mean() -> None:
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) is None
    assert mean([1.0, 2.0, 6.0]) == 3.0
    assert mean([]) is None
