"""The baseline: the project's own checks, run once before the team changes anything."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.contracts import CheckResult
from engineering_team.modes.baseline import run_baseline
from engineering_team.modes.baseline_report import (
    BASELINE_FILE,
    BaselineReport,
    failure_keys,
    load_baseline,
)
from engineering_team.modes.repo_analyzer import analyze_repo
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events

PYPROJECT = '[project]\nname = "calc"\nversion = "0.1.0"\n[tool.pytest.ini_options]\n'
CALC = "def add(a, b):\n    return a + b\n"
TESTS = """from calc import add


def test_add():
    assert add(1, 2) == 3


def test_add_is_broken():
    assert add(1, 2) == 4, "known pre-existing failure"
"""

MakeContext = Callable[..., RunContext]


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commands the checks run must find the interpreter (and pytest) running us."""

    scripts = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", scripts + ":" + __import__("os").environ["PATH"])


def project(make_context: MakeContext, files: dict[str, str]) -> RunContext:
    ctx = make_context("legacy")
    for name, text in files.items():
        ctx.workspace.write_file(name, text)
    return ctx


def test_a_seeded_failing_test_is_recorded_as_a_known_failure(make_context: MakeContext) -> None:
    ctx = project(
        make_context, {"pyproject.toml": PYPROJECT, "calc.py": CALC, "tests/test_calc.py": TESTS}
    )

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    (check,) = report.checks
    assert (check.kind, check.status, check.directory) == ("test", "failed", ".")
    assert "1 passed, 1 failed" in check.summary
    assert report.known_failures == ["test:.:tests/test_calc.py::test_add_is_broken"]
    assert check.failing == report.known_failures
    assert not report.green and report.revision


def test_the_baseline_is_saved_loaded_and_announced(make_context: MakeContext) -> None:
    ctx = project(
        make_context, {"pyproject.toml": PYPROJECT, "calc.py": CALC, "tests/test_calc.py": TESTS}
    )

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    saved = json.loads((ctx.workspace.root / BASELINE_FILE).read_text())
    assert saved["known_failures"] == report.known_failures
    assert load_baseline(ctx.workspace.root) == report
    (event,) = [
        e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "baseline.recorded"
    ]
    assert event.data["known_failures"] == 1
    assert (ctx.run_dir / "verification" / "baseline.json").is_file()  # not a verification round
    assert not (ctx.run_dir / "verification" / "round-0.json").exists()


def test_a_green_project_has_a_green_baseline(make_context: MakeContext) -> None:
    passing = TESTS.split("def test_add_is_broken")[0]
    ctx = project(
        make_context,
        {"pyproject.toml": PYPROJECT, "calc.py": CALC, "tests/test_calc.py": passing},
    )

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    assert report.green and report.known_failures == []
    assert report.lines() == [f"passed      {report.checks[0].name}: 1 passed, 0 failed, 0 skipped"]


def test_a_project_with_a_test_tool_but_no_tests_has_no_test_baseline(
    make_context: MakeContext,
) -> None:
    ctx = project(make_context, {"pyproject.toml": PYPROJECT, "calc.py": CALC})

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    (check,) = report.checks
    assert (check.status, check.summary, check.failing) == ("skipped", "no tests found", [])
    assert report.known_failures == [] and not report.green
    assert any("No tests were found in ." in n for n in report.notes)


def test_a_project_without_a_test_command_has_no_test_check_and_says_why(
    make_context: MakeContext,
) -> None:
    ctx = project(
        make_context,
        {"pyproject.toml": '[project]\nname = "calc"\nversion = "1"\n', "calc.py": CALC},
    )

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    assert report.checks == [] and not report.green
    assert any("No test command was detected" in n for n in report.notes)
    assert any("no baseline to record" in n for n in report.notes)


def test_a_directory_with_nothing_to_check_is_not_an_error(make_context: MakeContext) -> None:
    ctx = project(make_context, {"notes.txt": "hello\n"})

    report = run_baseline(ctx, analyze_repo(ctx.workspace.root))

    assert report.checks == [] and report.lines() == ["No checks could be run."]


def test_the_baseline_does_not_install_or_write_source(make_context: MakeContext) -> None:
    ctx = project(
        make_context, {"pyproject.toml": PYPROJECT, "calc.py": CALC, "tests/test_calc.py": TESTS}
    )
    before = {
        p.relative_to(ctx.workspace.root).as_posix(): p.read_bytes()
        for p in ctx.workspace.root.rglob("*")
        if p.is_file() and ".engineering-team" not in p.parts
    }

    run_baseline(ctx, analyze_repo(ctx.workspace.root))

    after = {
        p.relative_to(ctx.workspace.root).as_posix(): p.read_bytes()
        for p in ctx.workspace.root.rglob("*")
        if p.is_file()
        and ".engineering-team" not in p.parts
        and ".pytest_cache" not in p.parts
        and "__pycache__" not in p.parts
    }
    assert after == before


# -- stable keys -------------------------------------------------------------------------------


def result(kind: str, status: str = "failed", **fields: object) -> CheckResult:
    return CheckResult(id=kind, status=status, kind=kind, **fields)  # type: ignore[arg-type]


def test_lint_keys_leave_out_line_numbers_so_an_edit_does_not_make_a_failure_new() -> None:
    def diagnostics(line: int) -> CheckResult:
        return result(
            "lint",
            report={
                "diagnostics": [
                    {"file": "a.py", "line": line, "rule": "F401", "severity": "error"},
                    {"file": "a.py", "line": 1, "rule": "E501", "severity": "warning"},
                    {
                        "file": "b.py",
                        "line": 3,
                        "message": "Unused variable 'x' assigned",
                        "severity": "error",
                    },
                ]
            },
        )

    keys = failure_keys(diagnostics(3), "api")

    assert keys == failure_keys(diagnostics(300), "api")  # moved down the file: same failure
    assert keys == ["lint:api:a.py:F401", "lint:api:b.py:Unused variable 'x' assigned"]


def test_a_failure_without_detail_is_keyed_by_the_check_itself() -> None:
    assert failure_keys(result("build"), ".") == ["build:."]
    assert failure_keys(result("test", report={"failures": []}), "web") == ["test:web"]
    assert failure_keys(result("typecheck", report=None), ".") == ["typecheck:."]


def test_the_keys_are_capped() -> None:
    many = [{"test_id": f"t{n}"} for n in range(900)]

    assert len(failure_keys(result("test", report={"failures": many}), ".")) == 500


def test_a_report_round_trips_and_ignores_unknown_fields() -> None:
    report = BaselineReport(known_failures=["test:.:a"], notes=["n"])
    data = json.loads(report.model_dump_json())
    data["added_later"] = True

    assert BaselineReport.model_validate(data) == report
