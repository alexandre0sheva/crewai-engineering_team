"""User-supplied checks (``--checks FILE``): parsing, validation, and pinning for the run."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.runtime.context import RunContext
from engineering_team.verification.checks_file import (
    ChecksFileError,
    load_checks_file,
    parse_checks,
    pin_checks,
    pinned_checks,
)

MakeContext = Callable[..., RunContext]

VALID = """
checks:
  - id: unit
    name: Unit tests
    command: pytest -q tests
    kind: test
    timeout: 90
    criteria: [AC-1, AC-2]
  - id: style
    name: Style
    command: [ruff, check, .]
    kind: lint
    required: false
"""


def test_a_checks_file_becomes_check_specs() -> None:
    unit, style = parse_checks(VALID, source="checks.yaml")

    assert (unit.id, unit.name, unit.argv) == ("unit", "Unit tests", ["pytest", "-q", "tests"])
    assert unit.kind == "test" and unit.source == "user" and unit.required is True
    assert unit.timeout == 90 and unit.criteria_ids == ["AC-1", "AC-2"] and unit.type == "command"
    assert style.argv == ["ruff", "check", "."] and style.required is False


def test_a_bare_list_is_accepted_and_the_name_defaults_to_the_id() -> None:
    (check,) = parse_checks("- {id: smoke, command: make test}\n", source="c.yaml")

    assert check.name == "smoke" and check.kind == "custom" and check.argv == ["make", "test"]


def test_a_browser_script_check_names_a_script_instead_of_a_command() -> None:
    (check,) = parse_checks(
        "- {id: e2e, type: browser_script, script: e2e/flow.py, criteria: [AC-3]}\n",
        source="c.yaml",
    )

    assert check.type == "browser_script" and check.script == "e2e/flow.py" and check.argv == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("checks: [", "not valid YAML"),
        ("hello", "list of checks"),
        ("- {command: pytest}\n", "needs an id"),
        ("- {id: a}\n", "needs a command"),
        ("- {id: a, command: pytest}\n- {id: a, command: ruff .}\n", "used twice"),
        ("- {id: a, command: 'pytest && ruff .'}\n", "shell operator"),
        ("- {id: a, command: pytest, kind: fast}\n", "kind"),
        ("- {id: a, command: pytest, timeout: 0}\n", "timeout"),
        ("- {id: a, command: pytest, surprise: 1}\n", "unknown field"),
        ("- {id: a, type: browser_script, command: pytest}\n", "needs a script"),
        ("- {id: 'Bad Id', command: pytest}\n", "id"),
        ("- {id: a, command: pytest, criteria: AC-1}\n", "criteria"),
    ],
)
def test_a_bad_checks_file_says_what_to_fix(text: str, message: str) -> None:
    with pytest.raises(ChecksFileError, match=message):
        parse_checks(text, source="checks.yaml")


def test_a_checks_file_inside_the_project_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "checks.yaml").write_text(VALID, encoding="utf-8")

    with pytest.raises(ChecksFileError, match="outside the project"):
        load_checks_file(root / "checks.yaml", root)


def test_a_missing_checks_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ChecksFileError, match="not found"):
        load_checks_file(tmp_path / "nope.yaml", tmp_path / "project")


def test_the_checks_are_pinned_for_the_run_and_altering_the_pin_is_caught(
    make_context: MakeContext, tmp_path: Path
) -> None:
    ctx = make_context()
    source = tmp_path / "elsewhere" / "checks.yaml"
    source.parent.mkdir()
    source.write_text(VALID, encoding="utf-8")

    pin = pin_checks(ctx, source)

    assert pin.digest and [c.id for c in pinned_checks(ctx, pin.digest)] == ["unit", "style"]
    pinned = ctx.run_dir / "checks.yaml"
    assert pinned.read_text(encoding="utf-8") == VALID
    pinned.write_text(VALID.replace("required: false", "required: true"), encoding="utf-8")
    with pytest.raises(ChecksFileError, match="changed since the run started"):
        pinned_checks(ctx, pin.digest)


def test_without_a_checks_file_nothing_is_pinned_and_a_planted_copy_is_caught(
    make_context: MakeContext,
) -> None:
    ctx = make_context()

    assert pin_checks(ctx, None).digest == "" and pinned_checks(ctx, "") == []
    (ctx.run_dir / "checks.yaml").write_text("- {id: x, command: true}\n", encoding="utf-8")
    with pytest.raises(ChecksFileError, match="changed since the run started"):
        pinned_checks(ctx, "")
