"""The examples are what they claim: benchmark inputs, a real bug, honest sample reports."""

from __future__ import annotations

import filecmp
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
TASKS = ROOT / "benchmarks" / "tasks"
# example -> the benchmark task whose request and fixture it is, so the measured results apply
TASK_OF = {
    "greenfield-notes": "notes-cli",
    "feature-on-legacy": "legacy-feature",
    "bugfix": "seeded-bug",
}


def run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv], cwd=cwd, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.parametrize("name", TASK_OF)
def test_each_example_has_its_request_readme_and_report(name: str) -> None:
    folder = EXAMPLES / name

    assert (folder / "request.md").read_text(encoding="utf-8").strip()
    assert (folder / "README.md").read_text(encoding="utf-8").strip()
    assert (folder / "report.html").stat().st_size > 5_000
    assert (EXAMPLES / "README.md").read_text(encoding="utf-8").count(f"{name}/") >= 1


@pytest.mark.parametrize(("name", "task"), TASK_OF.items())
def test_the_requests_and_projects_are_the_benchmark_tasks_so_the_measurements_apply(
    name: str, task: str
) -> None:
    assert (EXAMPLES / name / "request.md").read_bytes() == (
        TASKS / task / "request.md"
    ).read_bytes()
    if name == "greenfield-notes":
        return
    comparison = filecmp.dircmp(EXAMPLES / name / "repo", TASKS / task / "fixture")
    assert not comparison.left_only + comparison.right_only + comparison.diff_files
    if name == "bugfix":
        assert (EXAMPLES / name / "trace.txt").read_bytes() == (
            TASKS / task / "trace.txt"
        ).read_bytes()


@pytest.mark.parametrize("name", ["feature-on-legacy", "bugfix"])
def test_the_example_projects_pass_their_own_tests_as_they_are(name: str) -> None:
    result = run("-m", "unittest", "discover", "-s", "tests", cwd=EXAMPLES / name / "repo")

    assert result.returncode == 0, result.stderr


def test_the_bug_in_the_bugfix_example_is_real(tmp_path: Path) -> None:
    cart = tmp_path / "cart.json"
    cart.write_text(json.dumps([]), encoding="utf-8")

    result = run("-m", "shop", "report", "--file", str(cart), cwd=EXAMPLES / "bugfix" / "repo")

    assert result.returncode != 0 and "ZeroDivisionError" in result.stderr
    assert "ZeroDivisionError: division by zero" in (EXAMPLES / "bugfix" / "trace.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("name", TASK_OF)
def test_the_sample_reports_are_verified_runs_without_machine_specific_paths(name: str) -> None:
    html = (EXAMPLES / name / "report.html").read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>") or html.startswith("<!DOCTYPE html>")
    assert "Verified" in html
    for leak in ("/Users/", "/private/", "/var/folders/", str(Path.home())):
        assert leak not in html, leak
    assert "<workspace>" in html


def test_the_readmes_say_the_reports_come_from_a_scripted_run() -> None:
    for name in TASK_OF:
        text = (EXAMPLES / name / "README.md").read_text(encoding="utf-8")
        assert "scripted" in text, name
    assert "teammates were scripted" in (EXAMPLES / "README.md").read_text(encoding="utf-8")
