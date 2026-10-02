"""The catalogue, its documentation, and the tool design rules every tool must meet."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.runtime.context import RunContext
from engineering_team.tools import CATALOGUE, GROUPS, build_tools

ROOT = Path(__file__).resolve().parent.parent
MakeContext = Callable[..., RunContext]


def _documented_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in (ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) >= 6 and cells[1].strip("`") in GROUPS:
            rows[cells[0].strip("`")] = cells
    return rows


def test_every_registered_tool_has_a_row_in_tools_md_and_vice_versa() -> None:
    documented = _documented_rows()

    assert set(documented) == {spec.name for spec in CATALOGUE}
    assert len(documented) == len(CATALOGUE)  # no duplicate rows or specs


def test_the_documentation_rows_agree_with_the_catalogue() -> None:
    documented = _documented_rows()
    yes_no = {True: "yes", False: "no"}

    for spec in CATALOGUE:
        cells = documented[spec.name]
        assert cells[1].strip("`") == spec.group, spec.name
        assert cells[2] == yes_no[spec.read_only], spec.name
        assert cells[3] == yes_no[spec.needs_network], spec.name
        assert cells[4] == yes_no[spec.needs_command_gate], spec.name


def test_each_spec_builds_a_tool_of_the_same_name(make_context: MakeContext) -> None:
    built = {tool.name for tool in build_tools(make_context())}

    assert built == {spec.name for spec in CATALOGUE}


def test_tool_names_are_unique_and_catalogue_order_is_stable(make_context: MakeContext) -> None:
    names = [spec.name for spec in CATALOGUE]

    assert len(names) == len(set(names))
    assert [tool.name for tool in build_tools(make_context())] == names


@pytest.mark.parametrize("group", GROUPS)
def test_groups_select_exactly_their_tools(make_context: MakeContext, group: str) -> None:
    selected = [tool.name for tool in build_tools(make_context(), groups=[group])]

    assert selected == [spec.name for spec in CATALOGUE if spec.group == group]
    assert selected


def test_read_only_mode_excludes_write_and_command_tools_in_every_group(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    read_only = {tool.name for tool in build_tools(ctx, read_only=True)}

    assert read_only == {spec.name for spec in CATALOGUE if spec.read_only}
    for spec in CATALOGUE:
        if not spec.read_only:
            assert spec.name not in read_only
    assert {tool.name for tool in build_tools(ctx, groups=["fs_write"], read_only=True)} == {
        "Workspace Changes"
    }


def test_unknown_groups_are_rejected(make_context: MakeContext) -> None:
    with pytest.raises(ValueError, match="Unknown tool group.*nope"):
        build_tools(make_context(), groups=["nope"])


def test_only_command_tools_need_the_command_gate_and_none_need_the_network() -> None:
    assert {spec.name for spec in CATALOGUE if spec.needs_command_gate} == {
        "Run Project Command",
        "Run Script",
    }
    assert not any(spec.needs_network for spec in CATALOGUE)
    assert {spec.name for spec in CATALOGUE if spec.group == "command" and spec.read_only} == {
        "List Scripts"
    }


def test_descriptions_are_written_for_an_llm_and_stay_short(make_context: MakeContext) -> None:
    for tool in build_tools(make_context()):
        words = len(tool.description.split())
        assert 8 <= words <= 120, f"{tool.name}: {words} words"


def test_summaries_are_one_line(make_context: MakeContext) -> None:
    for spec in CATALOGUE:
        assert spec.summary and "\n" not in spec.summary and spec.summary.endswith(".")


def test_no_tool_calls_subprocess_directly() -> None:
    """Execution goes through the backend, so the Docker sandbox covers every tool."""

    spawns = re.compile(r"^\s*(import subprocess|from subprocess import)", re.MULTILINE)
    allowed = {"local.py"}  # the local backend itself
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "engineering_team").rglob("*.py")
        if spawns.search(path.read_text(encoding="utf-8")) and path.name not in allowed
    ]

    assert offenders == []
