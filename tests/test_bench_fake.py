"""``bench run --fake`` end to end: the reference solutions pass, broken ones fail.

This is what proves the hidden checks discriminate, and the harness works from the command line
to the report, with no model. It is the offline benchmark CI runs (about a minute for each half).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from bench_helpers import SUITE
from typer.testing import CliRunner

from engineering_team.bench.results import collect_results, read_batch
from engineering_team.bench.tasks import load_suite
from engineering_team.cli.app import app

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")


def run_fake(out: Path, name: str, *extra: str):  # noqa: ANN201
    return CliRunner().invoke(
        app,
        ["--json", "bench", "run", "--fake", "--parallel", "4", "--suite", str(SUITE),
         "--out", str(out), "--batch", name, *extra],
    )  # fmt: skip


def test_every_reference_solution_passes_every_check(tmp_path: Path) -> None:
    result = run_fake(tmp_path, "reference")

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["planned"] == summary["passed"] == 10
    records = collect_results(tmp_path / "reference")
    assert [r.outcome for r in records] == ["passed"] * 10
    assert all(r.fake and r.cost_usd == 0 and r.tokens > 0 for r in records)
    assert all(c.passed for r in records for c in r.criteria)
    task_criteria = {t.id: [c.id for c in t.criteria] for t in load_suite(SUITE)}
    assert {r.task: [c.id for c in r.criteria] for r in records} == task_criteria

    batch_dir = tmp_path / "reference"
    meta = read_batch(batch_dir)
    assert meta["fake"] is True and meta["planned_runs"] == 10 and len(meta["tasks"]) == 10
    assert "Offline run" in (batch_dir / "report.md").read_text()
    rows = list(csv.DictReader((batch_dir / "results.csv").open()))
    assert len(rows) == 10 and {r["outcome"] for r in rows} == {"passed"}


def test_a_deliberately_broken_solution_fails_the_criteria_it_breaks(tmp_path: Path) -> None:
    result = run_fake(tmp_path, "broken", "--fake-solution", "broken")

    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["passed"] == 0
    records = {r.task: r for r in collect_results(tmp_path / "broken")}
    for task in load_suite(SUITE):
        record = records[task.id]
        assert record.outcome == "failed", f"{task.id} still passed with a broken solution"
        missed = set(record.failed_criteria)
        assert set(task.sabotage.fails) <= missed, (
            f"{task.id}: expected {task.sabotage.fails}, missed {missed}"
        )
        assert missed != {c.id for c in task.criteria}, f"{task.id}: the sabotage broke everything"


def test_broken_solutions_can_be_tolerated_when_failures_are_the_data(tmp_path: Path) -> None:
    result = run_fake(
        tmp_path,
        "tolerated",
        "--fake-solution",
        "broken",
        "--tasks",
        "notes-cli",
        "--allow-failures",
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["passed"] == 0
