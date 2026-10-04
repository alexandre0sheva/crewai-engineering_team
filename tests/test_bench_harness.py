"""The harness: planning, the budget ledger, preparing a run, judging it, and batches."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from bench_helpers import SUITE, record, stub_python

from engineering_team.bench import batch as batch_module
from engineering_team.bench.acceptance import source_root
from engineering_team.bench.batch import BudgetLedger, run_batch, task_digest
from engineering_team.bench.execution import ProcessRegistry, execute_run
from engineering_team.bench.metrics import event_metrics, parse_summary
from engineering_team.bench.plan import RunOptions, RunSpec, plan_runs
from engineering_team.bench.prepare import neutral_settings_environment, prepare
from engineering_team.bench.results import (
    RunRecord,
    collect_results,
    read_result,
    write_result,
)
from engineering_team.bench.tasks import BenchTask, load_suite
from engineering_team.runtime.events import JsonlSink

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")


@pytest.fixture(scope="module")
def tasks() -> dict[str, BenchTask]:
    return {t.id: t for t in load_suite(SUITE)}


def options(tmp_path: Path, **changes: object) -> RunOptions:
    values: dict[str, object] = {"batch": "t", "batch_dir": tmp_path / "t"}
    values.update(changes)
    return RunOptions(**values)  # type: ignore[arg-type]


# -- planning -------------------------------------------------------------------------------------


def test_runs_are_ordered_by_task_strategy_and_repeat(tasks: dict[str, BenchTask]) -> None:
    chosen = [tasks["todo-web"], tasks["notes-cli"]]

    specs, skipped = plan_runs(chosen, ["single", "pipeline"], 2)

    assert [s.key for s in specs] == [
        "notes-cli/single-1", "notes-cli/single-2", "notes-cli/pipeline-1", "notes-cli/pipeline-2",
        "todo-web/single-1", "todo-web/single-2", "todo-web/pipeline-1", "todo-web/pipeline-2",
    ]  # fmt: skip
    assert skipped == []


def test_repository_tasks_are_not_planned_for_strategies_they_cannot_use(
    tasks: dict[str, BenchTask],
) -> None:
    specs, skipped = plan_runs(
        [tasks["seeded-bug"], tasks["notes-cli"]], ["hierarchical", "pipeline"], 1
    )

    assert [s.key for s in specs] == [
        "notes-cli/hierarchical-1", "notes-cli/pipeline-1", "seeded-bug/pipeline-1",
    ]  # fmt: skip
    assert skipped == [("seeded-bug", "hierarchical")]


# -- the budget ledger ----------------------------------------------------------------------------


def test_a_run_reserves_its_cap_and_settles_with_what_it_cost() -> None:
    ledger = BudgetLedger(1.0)

    assert ledger.reserve(0.6) and not ledger.reserve(0.6)
    ledger.settle(0.6, 0.1)
    assert ledger.reserve(0.6)
    assert ledger.spent == pytest.approx(0.1)


def test_an_unknown_cost_counts_as_the_whole_reservation() -> None:
    ledger = BudgetLedger(1.0)
    assert ledger.reserve(0.5)

    ledger.settle(0.5, None)

    assert ledger.spent == 0.5
    assert not ledger.reserve(0.6)


def test_no_total_means_no_limit() -> None:
    ledger = BudgetLedger(None)

    assert ledger.reserve(1_000.0) and ledger.reserve(None)


def test_parallel_reservations_never_exceed_the_total() -> None:
    ledger = BudgetLedger(1.0)
    granted: list[bool] = []

    def attempt() -> None:
        granted.append(ledger.reserve(0.1))

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(granted) == 10


# -- reading what a run left behind ---------------------------------------------------------------


def test_the_summary_is_the_last_json_object_that_looks_like_one(tmp_path: Path) -> None:
    out = tmp_path / "stdout.log"
    out.write_text('Args: {}\n{"decoy": 1}\n{\n  "status": "succeeded",\n  "run_id": "r"\n}\n')

    assert parse_summary(out) == {"status": "succeeded", "run_id": "r"}


@pytest.mark.parametrize("text", ["", "no json here", "{broken", '{"other": 1}'])
def test_no_summary_is_an_empty_dict(tmp_path: Path, text: str) -> None:
    out = tmp_path / "stdout.log"
    out.write_text(text)

    assert parse_summary(out) == {}
    assert parse_summary(tmp_path / "missing") == {}


def test_event_metrics_count_repairs_tool_failures_setup_failures_and_cost(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "events.jsonl", "r")
    sink.emit("check.started", check="install", kind="setup")
    sink.emit("check.started", check="tests", kind="test")
    sink.emit("check.finished", check="install", status="unavailable")
    sink.emit("check.finished", check="tests", status="failed")  # not a setup check
    sink.emit("verify.repair", round=1)
    sink.emit("verify.repair", round=2)
    sink.emit("tool.call", tool="Run", ok=False)
    sink.emit("tool.call", tool="Run", ok=True)
    sink.emit(
        "llm.call", agent="dev", model="openai/gpt-6.1-sol",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000},
    )  # fmt: skip

    found = event_metrics(tmp_path / "events.jsonl")

    assert (found.repair_rounds, found.tool_failures, found.setup_failures) == (2, 1, 1)
    assert found.models == ["openai/gpt-6.1-sol"]
    assert found.usage["tokens"] == 1_100_000 and found.usage["tool_calls"] == 2
    assert found.usage["estimated_cost_usd"] == pytest.approx(3.0)


def test_event_metrics_of_a_missing_log_are_empty(tmp_path: Path) -> None:
    found = event_metrics(tmp_path / "nope.jsonl")

    assert found.usage == {} and found.repair_rounds == 0


# -- preparing a run ------------------------------------------------------------------------------


def argv_of(spec: RunSpec, tmp_path: Path, **changes: object) -> list[str]:
    return prepare(spec, options(tmp_path, **changes), tmp_path / "t" / "run").argv


def test_a_new_project_run_is_one_cli_subprocess_with_an_isolated_workspace(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    spec = RunSpec(tasks["notes-cli"], "single", 2)

    prepared = prepare(
        spec, options(tmp_path, provider="anthropic", profile="smoke", sandbox="docker",
                      run_budget_usd=1.5), tmp_path / "t" / "run",
    )  # fmt: skip

    run = tmp_path / "t" / "run"
    argv = prepared.argv
    assert argv[1:3] == ["-m", "engineering_team"] and "--json" in argv
    assert argv[argv.index("--workspace-root") + 1] == str(run / "ws")
    assert argv[argv.index("--run-id") + 1] == prepared.run_id
    assert argv[argv.index("new") :][:5] == ["new", "--request-file", str(run / "request.md"),
                                             "--project-name", "app"]  # fmt: skip
    assert argv[argv.index("--strategy") + 1] == "single"
    for flag, value in (
        ("--provider", "anthropic"),
        ("--profile", "smoke"),
        ("--sandbox", "docker"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert prepared.workspace == run / "ws" / "app"
    assert prepared.env["ENGINEERING_BUDGET_MAX_COST_USD"] == "1.5000"
    assert prepared.env["CREWAI_STORAGE_DIR"] == str(run / "crewai-storage")
    assert (run / "request.md").read_text() == tasks["notes-cli"].request_path.read_text()
    assert prepared.env["PYTHONPATH"].split(os.pathsep)[0] == source_root()  # the harness's code


def test_nothing_in_a_run_names_the_hidden_checks(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    for number, task in enumerate(tasks.values()):
        directory = tmp_path / str(number)
        prepared = prepare(RunSpec(task, "pipeline", 1), options(directory), directory / "t" / "r")

        assert str(task.root) not in " ".join(prepared.argv)
        assert "acceptance" not in " ".join(prepared.argv)
        leaked = [
            p for p in (directory / "t" / "r").rglob("*")
            if p.is_file() and (p.name == "checks.py" or "acceptance" in p.parts)
        ]  # fmt: skip
        assert leaked == []


def test_a_repository_task_starts_from_a_clean_repository_with_one_commit(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    spec = RunSpec(tasks["seeded-bug"], "pipeline", 1)

    prepared = prepare(spec, options(tmp_path), tmp_path / "t" / "run")

    repo = prepared.workspace
    assert repo == tmp_path / "t" / "run" / "repo"
    git = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), *a], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert git("status", "--porcelain") == ""
    assert git("rev-list", "--count", "HEAD") == "1"
    assert git("branch", "--show-current") == "main"
    argv = prepared.argv
    assert argv[argv.index("fix") :][:3] == ["fix", "--repo", str(repo)]
    assert "--strategy" not in argv
    trace = Path(argv[argv.index("--trace-file") + 1])
    assert trace.parent == tmp_path / "t" / "run"  # a copy, not the task's own file
    assert "ZeroDivisionError" in trace.read_text()


@pytest.mark.parametrize(
    ("task", "command", "extra"),
    [
        ("legacy-feature", "feature", ["--request-file"]),
        ("add-tests", "maintain", ["--task", "add-tests", "--goal-file"]),
        ("behaviour-refactor", "maintain", ["--task", "refactor", "--goal-file"]),
    ],
)
def test_each_mode_gets_its_own_command(
    tasks: dict[str, BenchTask], tmp_path: Path, task: str, command: str, extra: list[str]
) -> None:
    argv = argv_of(RunSpec(tasks[task], "pipeline", 1), tmp_path)

    assert command in argv
    assert all(item in argv for item in extra)


def test_a_live_run_ignores_ambient_settings_and_keeps_the_keys(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGINEERING_LEAD_MODEL", "openai/not-the-benchmark-model")
    monkeypatch.setenv("ENGINEERING_STRATEGY", "single")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-keep-me")
    config = tmp_path / "bench.toml"
    config.write_text("[budget]\nmax_repair_rounds = 1\n")

    prepared = prepare(
        RunSpec(tasks["notes-cli"], "pipeline", 1),
        options(tmp_path, run_budget_usd=2.0, config=str(config)),
        tmp_path / "t" / "r",
    )

    env, run = prepared.env, tmp_path / "t" / "r"
    assert env["ENGINEERING_LEAD_MODEL"] == "" and env["ENGINEERING_STRATEGY"] == ""
    assert env["ENGINEERING_CONFIG_FILE"] == "" and env["ENGINEERING_OVERRIDES"] == ""
    assert env["XDG_CONFIG_HOME"] == str(run / "xdg-config")  # not the user's config file
    assert env["ENGINEERING_BUDGET_MAX_COST_USD"] == "2.0000"  # the harness's own settings stay
    assert env["OPENAI_API_KEY"] == "sk-keep-me"
    assert prepared.argv[prepared.argv.index("--config") + 1] == str(config)


def test_the_team_really_ignores_an_ambient_model_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression test: a model set in the shell or in ``.env`` once reached live runs."""

    monkeypatch.setenv("ENGINEERING_LEAD_MODEL", "openai/not-the-benchmark-model")
    env = {**os.environ, **neutral_settings_environment(tmp_path), "PYTHONPATH": source_root()}

    shown = subprocess.run(
        [sys.executable, "-m", "engineering_team", "--json", "config", "show"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip

    assert "not-the-benchmark-model" not in shown.stdout
    assert "gpt-6.1-sol" in shown.stdout  # the shipped default


def test_a_fake_run_has_a_scrubbed_environment_and_no_cli(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("ENGINEERING_STRATEGY", "single")

    prepared = prepare(
        RunSpec(tasks["notes-cli"], "pipeline", 1),
        options(tmp_path, fake=True),
        tmp_path / "t" / "r",
    )

    assert "engineering_team.bench.fake_team" in prepared.argv
    assert "sk-test-secret" not in " ".join(prepared.env.values())
    assert not any(name.startswith("ENGINEERING_") for name in prepared.env)
    assert "OPENAI_API_KEY" not in prepared.env


# -- executing and judging a run (a stub team stands in for the CLI) -----------------------------


def run_with_stub(
    tasks: dict[str, BenchTask],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    task: str = "notes-cli",
    **task_changes: object,
) -> RunRecord:
    monkeypatch.setenv("STUB_MODE", mode)
    monkeypatch.setenv("STUB_REFERENCE", str(tasks[task].reference_dir))
    chosen = tasks[task].model_copy(update=task_changes)
    opts = options(tmp_path, python=stub_python(tmp_path), provider="openai", run_budget_usd=2.0)
    return execute_run(RunSpec(chosen, "pipeline", 1), opts, ProcessRegistry(), threading.Event())


def test_a_run_whose_workspace_meets_every_criterion_passes_and_records_its_metrics(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_with_stub(tasks, tmp_path, monkeypatch, "pass")

    assert result.outcome == "passed", result.reason
    assert [c.passed for c in result.criteria] == [True] * len(result.criteria)
    assert (result.repair_rounds, result.tool_failures, result.setup_failures) == (1, 1, 1)
    assert result.tokens == 1_100_000 and result.cost_usd == pytest.approx(3.0)
    assert result.models == ["openai/gpt-6.1-sol"]
    assert result.team_status == "succeeded" and result.exit_code == 0
    assert result.provider == "openai" and result.run_dir == "notes-cli/pipeline-1"
    stored = read_result(tmp_path / "t" / "notes-cli" / "pipeline-1")
    assert stored == result
    assert (tmp_path / "t" / "notes-cli" / "pipeline-1" / "stdout.log").is_file()


def test_the_summary_usage_wins_over_the_event_log(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_with_stub(tasks, tmp_path, monkeypatch, "summary-usage")

    assert result.tokens == 7 and result.cost_usd is None  # an unpriced model: unknown, not $0


def test_a_team_that_crashes_before_writing_anything_fails_every_criterion(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_with_stub(tasks, tmp_path, monkeypatch, "crash")

    assert result.outcome == "failed"
    assert result.exit_code == 1
    assert "the team ended" in result.reason
    assert result.failed_criteria == [c.id for c in tasks["notes-cli"].criteria]


def test_a_team_that_reports_success_but_built_nothing_still_fails(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_with_stub(tasks, tmp_path, monkeypatch, "empty")

    assert result.team_status == "succeeded"  # the team's own word is not evidence
    assert result.outcome == "failed"


def test_a_run_over_its_time_limit_is_a_timeout(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engineering_team.bench.execution.GRACE_SECONDS", 2)

    result = run_with_stub(tasks, tmp_path, monkeypatch, "hang", timeout_seconds=1)

    assert result.outcome == "timeout"
    assert "time limit" in result.reason
    assert result.duration_seconds < 30


def test_a_cancelled_batch_stops_the_team_process(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engineering_team.bench.execution.GRACE_SECONDS", 2)
    monkeypatch.setenv("STUB_MODE", "hang")
    monkeypatch.setenv("STUB_REFERENCE", str(tasks["notes-cli"].reference_dir))
    cancel = threading.Event()
    opts = options(tmp_path, python=stub_python(tmp_path))
    threading.Timer(1.5, cancel.set).start()

    result = execute_run(
        RunSpec(tasks["notes-cli"], "pipeline", 1), opts, ProcessRegistry(), cancel
    )

    assert result.outcome == "error" and "interrupted" in result.reason


def test_a_run_the_harness_cannot_set_up_is_an_error_not_a_failure(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    broken = tasks["seeded-bug"].model_copy(update={"root": tmp_path / "gone"})

    result = execute_run(
        RunSpec(broken, "pipeline", 1), options(tmp_path), ProcessRegistry(), threading.Event()
    )

    assert result.outcome == "error" and "setup failed" in result.reason
    assert not result.counted


# -- batches -------------------------------------------------------------------------------------


def fake_execute(costs: dict[str, float | None] | None = None, calls: list[str] | None = None):  # noqa: ANN202
    def execute(spec: RunSpec, opts: RunOptions, registry: object, cancel: object) -> RunRecord:
        if calls is not None:
            calls.append(spec.key)
        run_dir = opts.batch_dir / spec.task.id / f"{spec.strategy}-{spec.repeat}"
        run_dir.mkdir(parents=True, exist_ok=True)
        found = record(
            spec.task.id, spec.strategy, spec.repeat, cost_usd=(costs or {}).get(spec.key, 0.4)
        )
        write_result(run_dir, found)
        return found

    return execute


def test_a_batch_stops_starting_runs_when_the_budget_is_spent(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batch_module, "execute_run", fake_execute())
    specs, _ = plan_runs([tasks["notes-cli"]], ["pipeline"], 4)
    opts = options(tmp_path, run_budget_usd=0.5)

    results = run_batch(specs, opts, ledger=BudgetLedger(1.0))

    assert [r.outcome for r in results] == ["passed", "passed", "skipped", "skipped"]
    assert "budget" in results[2].reason
    assert [r.repeat for r in collect_results(opts.batch_dir)] == [1, 2, 3, 4]  # skips are recorded


def test_parallel_runs_are_recorded_in_a_stable_order(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batch_module, "execute_run", fake_execute())
    specs, _ = plan_runs([tasks["notes-cli"], tasks["todo-web"]], ["pipeline", "single"], 2)
    opts = options(tmp_path)

    seen: list[str] = []
    results = run_batch(specs, opts, parallel=4, on_result=lambda r: seen.append(r.task))

    assert [(r.task, r.strategy, r.repeat) for r in results] == [
        (s.task.id, s.strategy, s.repeat) for s in specs
    ]
    assert len(seen) == len(specs)
    assert [(r.task, r.strategy, r.repeat) for r in collect_results(opts.batch_dir)] == sorted(
        (s.task.id, s.strategy, s.repeat) for s in specs
    )


def test_resume_keeps_finished_runs_and_repeats_the_rest(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(batch_module, "execute_run", fake_execute(calls=calls))
    specs, _ = plan_runs([tasks["notes-cli"]], ["pipeline"], 3)
    opts = options(tmp_path)
    write_result(opts.batch_dir / "notes-cli" / "pipeline-1", record(repeat=1))
    write_result(opts.batch_dir / "notes-cli" / "pipeline-2", record(repeat=2, outcome="error"))

    results = run_batch(specs, options(tmp_path, resume=True))

    assert calls == ["notes-cli/pipeline-2", "notes-cli/pipeline-3"]
    assert [r.outcome for r in results] == ["passed", "passed", "passed"]


def test_an_interrupted_batch_stops_the_team_and_raises(
    tasks: dict[str, BenchTask], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped = threading.Event()

    class Registry(ProcessRegistry):
        def terminate_all(self) -> None:
            stopped.set()

    monkeypatch.setattr(batch_module, "ProcessRegistry", Registry)
    monkeypatch.setattr(batch_module, "execute_run", fake_execute())
    specs, _ = plan_runs([tasks["notes-cli"]], ["pipeline"], 2)

    def interrupt(_: RunRecord) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_batch(specs, options(tmp_path), on_result=interrupt)
    assert stopped.is_set()


def test_a_task_digest_changes_when_the_task_does(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    import shutil

    copy = tmp_path / "notes-cli"
    shutil.copytree(tasks["notes-cli"].root, copy)
    clone = tasks["notes-cli"].model_copy(update={"root": copy})
    before = task_digest(clone)

    assert before == task_digest(tasks["notes-cli"])
    (copy / "request.md").write_text("a different request\n")
    assert task_digest(clone) != before
    assert json.dumps(before)  # a short, printable hash
