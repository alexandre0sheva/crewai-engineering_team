"""Unit tests for the controller's red gate: what counts as a reproduction that failed."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.contracts import CheckResult
from engineering_team.modes.fix_input import (
    FixInput,
    compose_request,
    read_fix_input,
    write_fix_input,
)
from engineering_team.modes.fix_repro import not_a_failure


def result(status: str, exit_code: int | None, summary: str = "") -> CheckResult:
    return CheckResult(id="repro", status=status, exit_code=exit_code, summary=summary)  # type: ignore[arg-type]


PYTEST = ["python", "-m", "pytest", "tests/test_bug.py"]


def test_a_failing_exit_code_is_a_failure() -> None:
    assert not_a_failure(["python", "repro.py"], result("failed", 1)) is None
    assert not_a_failure(PYTEST, result("failed", 1)) is None


def test_a_pass_is_not_a_reproduction() -> None:
    reason = not_a_failure(PYTEST, result("passed", 0))

    assert reason is not None and "passed" in reason


@pytest.mark.parametrize(
    ("code", "says"),
    [(2, "collect"), (4, "usage"), (5, "no test")],
)
def test_pytest_exit_codes_that_are_not_a_failing_test_do_not_count(code: int, says: str) -> None:
    reason = not_a_failure(PYTEST, result("failed", code))

    assert reason is not None and says in reason.lower()


@pytest.mark.parametrize("code", [126, 127])
def test_a_command_that_could_not_start_is_not_a_failure(code: int) -> None:
    reason = not_a_failure(["python", "repro.py"], result("failed", code))

    assert reason is not None and "could not be started" in reason


def test_a_timeout_is_not_a_failure() -> None:
    reason = not_a_failure(["python", "repro.py"], result("failed", None, "timed out after 120s"))

    assert reason is not None and "timed out" in reason


def test_a_command_the_controller_could_not_run_is_not_a_failure() -> None:
    assert not_a_failure(PYTEST, result("unavailable", None)) is not None


def test_the_run_inputs_survive_in_the_run_directory(tmp_path: Path) -> None:
    write_fix_input(tmp_path, FixInput(repro="make bug", trace="boom", allow_unreproduced=True))

    again = read_fix_input(tmp_path)

    assert (again.repro, again.trace, again.allow_unreproduced) == ("make bug", "boom", True)
    assert read_fix_input(tmp_path / "missing") == FixInput()


def test_the_request_names_every_input_the_person_gave() -> None:
    text = compose_request("The list loses notes.", trace="Traceback ...", repro="make bug")

    assert text.startswith("The list loses notes.")
    assert "## Stack trace or log" in text and "Traceback ..." in text
    assert "## Reproduction command from the user" in text and "make bug" in text
    bare = compose_request(None, trace=None, repro="make bug")
    assert bare.startswith("Fix the bug")
