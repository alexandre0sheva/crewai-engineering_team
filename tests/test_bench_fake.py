"""``bench run --fake`` end to end: every reference passes, every broken solution and every
untouched fixture fails.

This is what proves the hidden checks discriminate, and the harness works from the command line
to the report, with no model. Tasks whose checks need Node or Go run here when that toolchain does
(``--acceptance-sandbox local``; npm packages from ``BENCH_NODE_MODULES``), and in Docker in the
``docker``-marked test.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from bench_helpers import SUITE, runnable_locally
from typer.testing import CliRunner

from engineering_team.bench.checklib import IMAGES
from engineering_team.bench.results import collect_results, read_batch
from engineering_team.bench.tasks import BenchTask, load_suite
from engineering_team.cli.app import app

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")


@pytest.fixture(scope="module")
def tasks() -> dict[str, BenchTask]:
    return {t.id: t for t in load_suite(SUITE) if runnable_locally(t)}


def run_fake(out: Path, name: str, ids: list[str], *extra: str):  # noqa: ANN201
    return CliRunner().invoke(
        app,
        ["--json", "bench", "run", "--fake", "--parallel", "4", "--suite", str(SUITE),
         "--out", str(out), "--batch", name, "--tasks", ",".join(ids),
         "--acceptance-sandbox", "local", *extra],
    )  # fmt: skip


def test_every_reference_solution_passes_every_check(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    result = run_fake(tmp_path, "reference", list(tasks))

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["planned"] == summary["passed"] == len(tasks)
    records = collect_results(tmp_path / "reference")
    assert [r.outcome for r in records] == ["passed"] * len(tasks)
    assert all(r.fake and r.cost_usd == 0 and r.tokens > 0 for r in records)
    assert all(c.passed for r in records for c in r.criteria)
    assert {r.task: [c.id for c in r.criteria] for r in records} == {
        t.id: [c.id for c in t.criteria] for t in tasks.values()
    }
    assert {r.task: (r.split, r.intent) for r in records} == {
        t.id: (t.split, t.intent) for t in tasks.values()
    }

    batch_dir = tmp_path / "reference"
    meta = read_batch(batch_dir)
    assert meta["fake"] is True and meta["planned_runs"] == len(tasks)
    assert "Offline run" in (batch_dir / "report.md").read_text()
    rows = list(csv.DictReader((batch_dir / "results.csv").open()))
    assert len(rows) == len(tasks) and {r["outcome"] for r in rows} == {"passed"}


def test_a_deliberately_broken_solution_fails_the_criteria_it_breaks(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    result = run_fake(tmp_path, "broken", list(tasks), "--fake-solution", "broken")

    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["passed"] == 0
    records = {r.task: r for r in collect_results(tmp_path / "broken")}
    for task in tasks.values():
        record = records[task.id]
        assert record.outcome == "failed", f"{task.id} still passed with a broken solution"
        missed = set(record.failed_criteria)
        assert set(task.sabotage.fails) <= missed, (
            f"{task.id}: expected {task.sabotage.fails}, missed {missed}"
        )
        assert missed != {c.id for c in task.criteria}, f"{task.id}: the sabotage broke everything"


def test_the_untouched_fixture_fails_every_task(
    tasks: dict[str, BenchTask], tmp_path: Path
) -> None:
    result = run_fake(tmp_path, "fixture", list(tasks), "--fake-solution", "fixture")

    assert result.exit_code == 3, result.output
    records = {r.task: r for r in collect_results(tmp_path / "fixture")}
    for task in tasks.values():
        record = records[task.id]
        assert record.outcome == "failed", f"{task.id}: the fixture alone got {record.outcome}"
        assert record.failed_criteria, f"{task.id}: no criterion tells the fixture from the work"


def test_broken_solutions_can_be_tolerated_when_failures_are_the_data(tmp_path: Path) -> None:
    result = run_fake(
        tmp_path, "tolerated", ["notes-cli"], "--fake-solution", "broken", "--allow-failures"
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["passed"] == 0


def test_a_check_without_its_toolchain_is_an_error_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    tools = tmp_path / "bin"  # git (to make the fixture repository), but no node, go or docker
    tools.mkdir()
    (tools / "git").symlink_to(shutil.which("git") or "/usr/bin/git")
    monkeypatch.setenv("PATH", str(tools))

    result = CliRunner().invoke(
        app,
        ["--json", "bench", "run", "--fake", "--suite", str(SUITE), "--out", str(tmp_path),
         "--batch", "missing", "--tasks", "go-cli-feature"],
    )  # fmt: skip

    (record,) = collect_results(tmp_path / "missing")
    go_task = next(t for t in load_suite(SUITE) if t.id == "go-cli-feature")
    assert record.outcome == "error", record.reason
    assert record.failed_criteria == []
    assert record.errored_criteria == [c.id for c in go_task.criteria]
    assert "could not judge" in record.reason
    assert all("docker is not on PATH" in c.detail for c in record.criteria if c.error)
    assert result.exit_code == 3


@pytest.mark.docker
def test_toolchain_tasks_validate_in_their_docker_images(tmp_path: Path) -> None:
    import subprocess

    toolchain_tasks = [t for t in load_suite(SUITE) if t.toolchains]
    for name, image in IMAGES.items():
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            pytest.skip(f"build {image} first: docker build -t {image} benchmarks/images/{name}")
    ids = [t.id for t in toolchain_tasks]
    for solution, outcome in (("reference", "passed"), ("broken", "failed"), ("fixture", "failed")):
        CliRunner().invoke(
            app,
            ["--json", "bench", "run", "--fake", "--suite", str(SUITE), "--out", str(tmp_path),
             "--batch", solution, "--tasks", ",".join(ids), "--fake-solution", solution,
             "--allow-failures"],
        )  # fmt: skip
        records = collect_results(tmp_path / solution)
        assert {r.task: r.outcome for r in records} == dict.fromkeys(ids, outcome)
