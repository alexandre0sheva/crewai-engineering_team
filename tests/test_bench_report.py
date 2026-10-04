"""``bench report``: pass rates with intervals, cost per success, every failure, and the cost
estimate behind ``--dry-run``."""

from __future__ import annotations

import csv
import io

import pytest
from bench_helpers import SUITE, failing, record

from engineering_team.bench import estimate
from engineering_team.bench.plan import RunSpec
from engineering_team.bench.report import aggregate, group_by, render_csv, render_markdown
from engineering_team.bench.stats import wilson_interval
from engineering_team.bench.tasks import load_suite
from engineering_team.settings import load_settings


def mixed():  # noqa: ANN201
    return [
        record("notes-cli", "pipeline", 1, cost_usd=1.0),
        record("notes-cli", "pipeline", 2, cost_usd=2.0, outcome="failed",
               criteria=failing("ids-not-reused"), reason="criteria not met: ids-not-reused"),
        record("todo-web", "pipeline", 1, cost_usd=3.0),
        record("notes-cli", "single", 1, cost_usd=0.5, outcome="failed", reason="no good"),
        record("todo-web", "single", 1, outcome="timeout", cost_usd=1.5, reason="too slow"),
        record("cron-scheduler", "single", 1, outcome="error", cost_usd=None, reason="setup"),
        record("cron-scheduler", "pipeline", 1, outcome="skipped", cost_usd=None, reason="budget"),
    ]  # fmt: skip


def test_pass_rate_counts_only_runs_that_counted_and_has_a_wilson_interval() -> None:
    group = aggregate("pipeline", [r for r in mixed() if r.strategy == "pipeline"])

    assert (group.runs, group.counted, group.passed) == (4, 3, 2)
    assert group.rate == pytest.approx(2 / 3)
    assert (group.low, group.high) == wilson_interval(2, 3)


def test_cost_per_success_includes_what_the_failures_cost() -> None:
    group = aggregate("pipeline", [r for r in mixed() if r.strategy == "pipeline"])

    assert group.cost_total == pytest.approx(6.0)
    assert group.cost_per_success == pytest.approx(3.0)  # $6 spent for 2 successes
    assert group.cost_mean == pytest.approx(2.0)


def test_an_unknown_cost_makes_the_aggregate_unknown_not_cheaper() -> None:
    group = aggregate("x", [record(cost_usd=1.0), record(repeat=2, cost_usd=None)])

    assert group.cost_total is None and group.cost_mean is None and group.cost_per_success is None


def test_a_group_with_no_counted_runs_has_no_rate() -> None:
    group = aggregate("x", [record(outcome="skipped")])

    assert group.rate is None and (group.low, group.high) == (0.0, 1.0)


def test_groups_are_sorted_by_label() -> None:
    groups = group_by(mixed(), lambda r: r.strategy)

    assert [g.label for g in groups] == ["pipeline", "single"]


def test_the_markdown_report_has_the_tables_and_lists_every_failure() -> None:
    text = render_markdown(
        {"batch": "demo", "strategies": ["pipeline", "single"], "repeat": 2}, mixed()
    )

    assert text.startswith("# Benchmark report: demo")
    for heading in (
        "## Results by strategy",
        "## By task kind and subset",
        "## By task",
        "## Failures (5)",
    ):
        assert heading in text
    assert "| pipeline | 2/3 | 67% (21%–94%) |" in text
    assert "Wilson 95 %" in text
    failures = text.split("## Failures")[1]
    for expected in ("ids-not-reused", "no good", "too slow", "setup", "budget"):
        assert expected in failures
    assert "$3.00" in text  # the pipeline's cost per success
    assert "2 run(s) did not count towards pass rates" in text


def test_a_small_sample_is_flagged_and_a_fake_run_is_labelled() -> None:
    text = render_markdown({"batch": "demo", "fake": True}, [record(fake=True)])

    assert "Small sample" in text
    assert "Offline run (`--fake`)" in text
    assert "says nothing about the team's quality" in text


def test_a_large_sample_is_not_flagged() -> None:
    many = [record(repeat=n) for n in range(1, 31)]

    assert "Small sample" not in render_markdown({"batch": "demo"}, many)


def test_not_applicable_pairs_are_listed() -> None:
    text = render_markdown(
        {"batch": "d", "not_applicable": [{"task": "seeded-bug", "strategy": "single"}]}, mixed()
    )

    assert "seeded-bug × single" in text


def test_a_report_of_nothing_says_so() -> None:
    assert "No results found" in render_markdown({"batch": "empty"}, [])


def test_the_csv_has_one_row_per_run_in_stable_order_and_unknown_costs_are_blank() -> None:
    rows = list(csv.DictReader(io.StringIO(render_csv(mixed()))))

    assert len(rows) == 7
    assert rows[0]["task"] == "notes-cli" and rows[0]["cost_usd"] == "1.000000"
    unknown = next(r for r in rows if r["outcome"] == "error")
    assert unknown["cost_usd"] == ""
    failed = next(r for r in rows if r["failed_criteria"])
    assert failed["failed_criteria"] == "ids-not-reused"


# -- the dry-run estimate -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def specs() -> list[RunSpec]:
    tasks = {t.id: t for t in load_suite(SUITE)}
    return [RunSpec(tasks["notes-cli"], s, n) for s in ("pipeline", "single") for n in (1, 2)]


@pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
def test_the_estimate_prices_every_strategy_from_the_price_table(specs: list[RunSpec]) -> None:
    settings = load_settings(overrides={"provider": "openai"})

    found = estimate.estimate_batch(specs, settings)

    assert found.runs == 4
    by_strategy = {r.strategy: r.per_run_usd for r in found.rows}
    assert by_strategy["pipeline"] and by_strategy["single"]
    assert by_strategy["pipeline"] > by_strategy["single"] > 0
    assert found.total_usd == pytest.approx(2 * by_strategy["pipeline"] + 2 * by_strategy["single"])
    low, high = found.range_usd or (0, 0)
    assert low < found.total_usd < high
    assert found.unpriced_models == []


@pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
def test_a_local_model_costs_nothing_and_a_task_scale_scales_the_cost(specs: list[RunSpec]) -> None:
    free = estimate.estimate_batch(specs, load_settings(overrides={"provider": "ollama"}))
    paid = load_settings(overrides={"provider": "google"})

    assert free.total_usd == 0.0
    small = estimate.per_run_cost(paid, "single", 1.0)[0]
    big = estimate.per_run_cost(paid, "single", 2.0)[0]
    assert small and big == pytest.approx(2 * small)


@pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
def test_an_unpriced_model_makes_the_estimate_unknown(specs: list[RunSpec]) -> None:
    settings = load_settings(
        overrides={"provider": "openai", "models.tiers.worker.model": "openai/not-priced-yet"}
    )

    found = estimate.estimate_batch(specs, settings)

    assert found.total_usd is None and found.range_usd is None
    assert "openai/not-priced-yet" in found.unpriced_models
