"""``bench run --agent``: external agents on the same tasks and hidden checks as the team, end to
end with fake binaries on ``PATH`` (no real agent, no network, no model)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from agent_fakes import install_fake_agents
from bench_helpers import SUITE
from typer.testing import CliRunner

from engineering_team.bench.export import RESULTS_FILE, results_document
from engineering_team.bench.report import render_markdown
from engineering_team.bench.results import RunRecord, collect_results, read_batch
from engineering_team.bench.spend import SpendLedger
from engineering_team.cli.app import app

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
TASKS = "notes-cli,seeded-bug"
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_USE_BEDROCK")


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    """A suite whose tasks are the real ones and whose ledger is a temp file."""

    root = tmp_path / "suite"
    root.mkdir()
    (root / "tasks").symlink_to(SUITE / "tasks")
    (root / "budget.toml").write_text("total_usd = 15.0\n[allocations]\nT33 = 9.70\n")
    return root


@pytest.fixture
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite: Path) -> Path:
    """Fake ``claude``, ``codex``, ``opencode`` and ``aider`` first on ``PATH``, no keys set."""

    bin_dir = install_fake_agents(tmp_path / "bin", SUITE)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_AGENT_LOG", str(tmp_path / "calls.jsonl"))
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return bin_dir


def run_bench(suite: Path, tmp_path: Path, *args: str, batch: str = "b"):  # noqa: ANN201
    return CliRunner().invoke(
        app,
        ["--json", "bench", "run", "--suite", str(suite), "--out", str(tmp_path / "out"),
         "--batch", batch, "--acceptance-sandbox", "local", *args],
    )  # fmt: skip


def batch_dir(tmp_path: Path, name: str = "b") -> Path:
    return tmp_path / "out" / name


def calls(tmp_path: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]


def test_a_fake_team_and_fake_agents_make_one_mixed_comparison(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    result = run_bench(
        suite, tmp_path, "--fake", "--agent", "claude-code,codex", "--strategy", "pipeline",
        "--tasks", TASKS, "--parallel", "2",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["planned"] == summary["passed"] == 6
    records = collect_results(batch_dir(tmp_path))
    assert {r.contender for r in records} == {"pipeline", "claude-code", "codex"}
    assert [r.outcome for r in records] == ["passed"] * 6
    team = [r for r in records if r.agent is None]
    external = [r for r in records if r.agent]
    assert all(r.fake and r.billing == "none" for r in team)
    assert all(not r.fake and r.agent_version == f"{r.strategy.split('-')[0]} 9.9.9 (fake)"
               for r in external)  # fmt: skip
    report = (batch_dir(tmp_path) / "report.md").read_text()
    assert "Paired comparison" in report and "claude-code vs codex" in report
    assert "Mixed batch" in report and "Agent claude-code:" in report
    results = json.loads((batch_dir(tmp_path) / RESULTS_FILE).read_text())
    assert [c["name"] for c in results["contenders"]] == ["claude-code", "codex", "pipeline"]
    assert {c["kind"] for c in results["contenders"]} == {"agent", "strategy"}
    assert len(results["runs"]) == 6 and len(results["paired"]) == 3


def test_agents_alone_replace_the_default_pipeline(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    result = run_bench(suite, tmp_path, "--agent", "opencode", "--budget-usd", "2",
                       "--tasks", "notes-cli", "--allocation", "T33")  # fmt: skip

    assert result.exit_code == 0, result.output
    assert {r.strategy for r in collect_results(batch_dir(tmp_path))} == {"opencode"}
    assert read_batch(batch_dir(tmp_path))["strategies"] == ["opencode"]


def test_an_agent_gets_the_same_request_and_a_fresh_copy_of_the_fixture(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    result = run_bench(suite, tmp_path, "--agent", "claude-code", "--tasks", TASKS,
                       "--allocation", "T33")  # fmt: skip

    assert result.exit_code == 0, result.output
    by_cwd = {Path(str(c["cwd"])).parts[-3:]: c for c in calls(tmp_path)}
    greenfield = next(
        c for c in calls(tmp_path) if str(c["cwd"]).endswith("notes-cli/claude-code-1/ws/app")
    )
    assert greenfield["listing"] == []  # an empty project directory, as the team gets
    assert greenfield["argv"][1] == (SUITE / "tasks/notes-cli/request.md").read_text().strip()
    repo = next(
        c for c in calls(tmp_path) if str(c["cwd"]).endswith("seeded-bug/claude-code-1/repo")
    )
    assert ".git" in repo["listing"] and "acceptance" not in repo["listing"]
    prompt = str(repo["argv"][1])
    assert prompt.startswith((SUITE / "tasks/seeded-bug/request.md").read_text().strip())
    assert "stack trace" in prompt and len(by_cwd) == 2
    # the hidden checks, references and sabotage are nowhere in a run directory
    names = {p.name for p in batch_dir(tmp_path).rglob("*")}
    assert not names & {"acceptance", "checks.py", "reference", "task.yaml"}


def test_the_checks_judge_an_agent_not_what_it_says(
    suite: Path, agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGENT_MODE", "empty")  # it reports success and builds nothing

    result = run_bench(suite, tmp_path, "--agent", "codex", "--tasks", "notes-cli",
                       "--allocation", "T33")  # fmt: skip

    assert result.exit_code == 3
    (record,) = collect_results(batch_dir(tmp_path))
    assert record.outcome == "failed" and record.exit_code == 0
    assert record.reason.startswith("criteria not met:") and record.failed_criteria


def test_an_agent_that_crashes_is_a_failed_run_with_its_exit_code(
    suite: Path, agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGENT_MODE", "crash")

    result = run_bench(suite, tmp_path, "--agent", "opencode", "--budget-usd", "2",
                       "--tasks", "notes-cli", "--allocation", "T33",
                       "--allow-failures")  # fmt: skip

    assert result.exit_code == 0
    (record,) = collect_results(batch_dir(tmp_path))
    assert record.outcome == "failed" and "opencode ended: exit code 3" in record.reason


def test_a_plan_billed_run_is_labelled_never_zero_and_costs_the_budget_nothing(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    result = run_bench(suite, tmp_path, "--agent", "claude-code", "--tasks", "notes-cli",
                       "--allocation", "T33")  # fmt: skip

    assert result.exit_code == 0, result.output
    (record,) = collect_results(batch_dir(tmp_path))
    assert record.billing == "subscription"
    assert record.cost_usd is None and record.equivalent_cost_usd == pytest.approx(0.42)
    assert "plan (≈$0.42)" in (batch_dir(tmp_path) / "report.md").read_text()
    book = SpendLedger(suite)
    (entry,) = book.entries()
    assert (entry.billing, entry.actual_usd, entry.charged) == ("subscription", 0.0, 0.0)
    assert book.spent() == 0 and book.rows()[0]["subscription_runs"] == 1


def test_an_api_billed_run_is_capped_charged_and_recorded(
    suite: Path, agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    result = run_bench(suite, tmp_path, "--agent", "claude-code", "--tasks", "notes-cli",
                       "--allocation", "T33", "--budget-usd", "2", "--run-budget-usd", "0.5",
                       "--agent-model", "claude-code=sonnet")  # fmt: skip

    assert result.exit_code == 0, result.output
    (record,) = collect_results(batch_dir(tmp_path))
    assert record.billing == "api" and record.cost_usd == pytest.approx(0.42)
    argv = calls(tmp_path)[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5000"
    assert argv[argv.index("--model") + 1] == "sonnet"
    (entry,) = SpendLedger(suite).entries()
    assert entry.billing == "api" and entry.estimated_usd == 0.5
    assert entry.actual_usd == pytest.approx(0.42)


def test_an_api_billed_agent_needs_a_budget_and_an_allocation(
    suite: Path, agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    without_budget = run_bench(suite, tmp_path, "--agent", "codex", "--allocation", "T33")
    without_allocation = run_bench(suite, tmp_path, "--agent", "codex", "--budget-usd", "1")

    assert without_budget.exit_code == 2 and "--budget-usd" in without_budget.output
    assert without_allocation.exit_code == 2 and "--allocation" in without_allocation.output
    assert not (tmp_path / "calls.jsonl").exists()  # nothing was started


def test_a_run_the_ledger_cannot_afford_is_refused_before_it_starts(
    suite: Path, agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    (suite / "budget.toml").write_text("total_usd = 15.0\n[allocations]\nT33 = 0.10\n")

    result = run_bench(suite, tmp_path, "--agent", "claude-code", "--tasks", "notes-cli",
                       "--allocation", "T33", "--budget-usd", "1")  # fmt: skip

    assert result.exit_code == 2 and "Refused" in result.output
    assert not (tmp_path / "calls.jsonl").exists()


def test_a_missing_agent_stops_the_batch_instead_of_being_dropped(
    suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = run_bench(suite, tmp_path, "--fake", "--agent", "aider", "--tasks", "notes-cli")

    assert result.exit_code == 2 and "Not on PATH: aider" in result.output


def test_reviews_are_left_to_the_team(suite: Path, agents: Path, tmp_path: Path) -> None:
    result = run_bench(suite, tmp_path, "--agent", "codex", "--allocation", "T33",
                       "--tasks", "notes-cli,review-seeded-defects")  # fmt: skip

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["planned"] == 1
    assert read_batch(batch_dir(tmp_path))["not_applicable"] == [
        {"task": "review-seeded-defects", "strategy": "codex"}
    ]
    assert "external agents skip reviews" in (batch_dir(tmp_path) / "report.md").read_text()


def test_a_pinned_model_is_passed_and_recorded(suite: Path, agents: Path, tmp_path: Path) -> None:
    result = run_bench(suite, tmp_path, "--agent", "codex", "--tasks", "notes-cli",
                       "--allocation", "T33", "--agent-model", "codex=gpt-test")  # fmt: skip

    assert result.exit_code == 0, result.output
    argv = calls(tmp_path)[0]["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-test"
    (record,) = collect_results(batch_dir(tmp_path))
    assert record.models == ["gpt-test"]
    assert read_batch(batch_dir(tmp_path))["agents"]["codex"]["model"] == "gpt-test"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--agent", "gemini"], "Unknown agent: gemini"),
        (["--agent", "codex", "--agent-model", "aider=x"], "not one of the selected --agent"),
        (["--agent", "codex", "--agent-model", "codex"], "takes AGENT=VALUE"),
        (["--agent", "codex", "--agent-billing", "codex=free"], "must be one of api, subscription"),
    ],
)
def test_bad_agent_options_are_usage_errors(
    args: list[str], message: str, suite: Path, agents: Path, tmp_path: Path
) -> None:
    result = run_bench(suite, tmp_path, "--tasks", "notes-cli", *args)

    assert result.exit_code == 2 and message in result.output


def test_a_dry_run_says_what_it_cannot_price(suite: Path, agents: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["bench", "run", "--dry-run", "--suite", str(suite), "--agent", "claude-code",
         "--strategy", "pipeline", "--tasks", "notes-cli", "--provider", "openai"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "1 run(s) by external agents are not in that figure" in result.output


def test_the_report_command_writes_results_json_for_a_finished_batch(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    run_bench(suite, tmp_path, "--agent", "codex", "--tasks", "notes-cli", "--allocation", "T33")
    (batch_dir(tmp_path) / RESULTS_FILE).unlink()

    result = CliRunner().invoke(
        app, ["bench", "report", "b", "--suite", str(suite), "--out", str(tmp_path / "out")]
    )

    assert result.exit_code == 0, result.output
    assert (batch_dir(tmp_path) / RESULTS_FILE).is_file()


def test_a_0_2_0_result_still_reads_as_a_team_run(tmp_path: Path) -> None:
    old = {"batch": "b", "task": "t", "strategy": "single", "repeat": 1, "kind": "greenfield",
           "mode": "new", "subset": "dev", "outcome": "passed", "schema_version": 1}  # fmt: skip

    record = RunRecord.model_validate(old)

    assert record.agent is None and record.contender == "single" and record.billing == "api"
    assert results_document({}, [record])["contenders"][0]["kind"] == "strategy"
    assert "single" in render_markdown({}, [record])


def test_the_results_schema_is_one_for_team_and_agents(
    suite: Path, agents: Path, tmp_path: Path
) -> None:
    run_bench(suite, tmp_path, "--fake", "--agent", "codex", "--strategy", "single",
              "--tasks", "notes-cli", "--allocation", "T33")  # fmt: skip

    document = json.loads((batch_dir(tmp_path) / RESULTS_FILE).read_text())

    assert set(document) >= {"export_version", "batch", "contenders", "paired", "runs", "agents"}
    agent, team = document["runs"]  # in order of task, then strategy or agent
    assert set(team) == set(agent)  # one record schema
    assert (team["agent"], agent["agent"]) == (None, "codex") and team["strategy"] == "single"
    assert {"name", "kind", "pass_rate", "wilson_low", "wilson_high", "cost_per_success_usd",
            "billing"} <= set(document["contenders"][0])  # fmt: skip


def test_copying_the_batch_keeps_it_readable(suite: Path, agents: Path, tmp_path: Path) -> None:
    run_bench(suite, tmp_path, "--agent", "codex", "--tasks", "notes-cli", "--allocation", "T33")
    moved = tmp_path / "moved"

    shutil.copytree(batch_dir(tmp_path), moved)

    assert [r.agent for r in collect_results(moved)] == ["codex"]
