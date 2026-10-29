"""The paired comparison of strategies and external agents, and who may spend what."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from agent_fakes import install_fake_agents
from bench_helpers import SUITE, record

from engineering_team.bench.agents import build_agent
from engineering_team.bench.compare import pairings, render_comparison, task_rates
from engineering_team.bench.plan import RunOptions, applies, plan_runs
from engineering_team.bench.report import aggregate, cost_cell
from engineering_team.bench.tasks import load_suite
from engineering_team.execution.local import LocalBackend


def runs(contender: str, **outcomes: str):  # noqa: ANN201
    """Records of one contender: ``task="passed,failed"`` gives a run per outcome."""

    agent = None if contender in ("pipeline", "single") else contender
    return [
        record(task, contender, n, outcome=outcome, agent=agent)
        for task, text in outcomes.items()
        for n, outcome in enumerate(text.split(","), 1)
    ]


def test_a_contender_wins_a_task_when_its_pass_rate_is_higher() -> None:
    records = [
        *runs("pipeline", a="passed,passed", b="failed,failed", c="passed,failed", d="passed"),
        *runs("codex", a="passed,passed", b="passed,passed", c="failed,failed", e="passed"),
    ]

    (pair,) = pairings(task_rates(records))

    # a: tie; b: codex wins; c: pipeline wins; d and e are not shared and are not compared
    assert (pair.a, pair.b) == ("codex", "pipeline")
    assert (pair.tasks, pair.a_wins, pair.b_wins, pair.ties) == (3, 1, 1, 1)


def test_runs_that_did_not_count_are_not_compared() -> None:
    records = [*runs("pipeline", a="error,skipped"), *runs("codex", a="passed")]

    assert pairings(task_rates(records)) == []  # one side has nothing to compare
    assert render_comparison(records) == []


def test_the_comparison_is_markdown_only_with_two_contenders() -> None:
    one = runs("pipeline", a="passed")

    text = "\n".join(render_comparison([*one, *runs("claude-code", a="failed")]))

    assert render_comparison(one) == []
    assert "claude-code vs pipeline" in text and "| a | 0/1 | 1/1 |" in text


def test_a_plan_billed_group_reads_plan_never_zero_dollars() -> None:
    plan = [record(billing="subscription", cost_usd=None, equivalent_cost_usd=0.5, agent="codex")]
    unpriced = [record(cost_usd=None)]
    paid = [record(cost_usd=0.25)]

    cells = [cost_cell(g, g.cost_mean) for g in map(aggregate, "cup", (plan, unpriced, paid))]

    assert cells == ["plan (≈$0.50)", "unknown", "$0.25"]


def test_an_agent_has_no_sessions_or_model_calls_in_the_team_columns() -> None:
    agent = record(agent="codex", sessions=1, model_calls=4)
    team = record(sessions=3, model_calls=10)

    assert aggregate("codex", [agent]).sessions_mean is None
    assert aggregate("pipeline", [team]).sessions_mean == 3


def options(tmp_path: Path, **fields: object) -> RunOptions:
    return RunOptions(batch="b", batch_dir=tmp_path, **fields)  # type: ignore[arg-type]


def test_only_runs_that_spend_money_get_a_cap(tmp_path: Path) -> None:
    api = options(
        tmp_path, run_budget_usd=1.0, agent_billing={"codex": "api", "claude-code": "subscription"}
    )
    offline = options(tmp_path, run_budget_usd=1.0, fake=True)

    assert api.cap_for("pipeline") == 1.0 and api.cap_for("codex") == 1.0
    assert api.cap_for("claude-code") is None  # a plan spends no money
    assert offline.cap_for("pipeline") is None  # a scripted team spends none
    assert offline.cap_for("codex") == 1.0  # an external agent is never offline


def test_external_agents_take_every_task_but_reviews() -> None:
    tasks = {t.id: t for t in load_suite(SUITE)}

    assert not applies(tasks["review-seeded-defects"], "codex")
    assert applies(tasks["seeded-bug"], "codex") and applies(tasks["notes-cli"], "aider")
    assert not applies(tasks["seeded-bug"], "single")  # the team's own rule is unchanged
    specs, skipped = plan_runs(list(tasks.values()), ["pipeline", "codex"], 1)
    assert ("review-seeded-defects", "codex") in skipped
    assert ("review-seeded-defects", "pipeline") not in skipped
    assert {s.strategy for s in specs} == {"pipeline", "codex"}


def test_a_cancelled_batch_stops_the_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = install_fake_agents(tmp_path / "bin", SUITE)
    cancel = threading.Event()
    agent = build_agent(
        "claude-code", LocalBackend(tmp_path / "logs", cancel),
        env={"PATH": str(bin_dir), "FAKE_AGENT_MODE": "hang"},
    )  # fmt: skip
    threading.Timer(1.0, cancel.set).start()

    run = agent.run(tmp_path, "Do it.", timeout=60)

    assert run.cancelled and not run.timed_out and run.wall_s < 30
