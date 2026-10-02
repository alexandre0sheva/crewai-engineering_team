"""The ``code_intel`` tools over the polyglot fixture repository and a synthetic Git history."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import Toolbox
from git_helpers import commit, init_repo
from polyglot_repo import line_of, write_polyglot

from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import CATALOGUE, build_tools

MakeToolbox = Callable[..., Toolbox]
TOOLS = [spec.name for spec in CATALOGUE if spec.group == "code_intel"]


@pytest.fixture
def box(make_toolbox: MakeToolbox) -> Toolbox:
    toolbox = make_toolbox(groups=["code_intel"])
    write_polyglot(toolbox.workspace.root)
    return toolbox


def test_the_group_has_the_nine_planned_tools_and_all_are_read_only() -> None:
    assert TOOLS == [
        "Find Symbol",
        "Show Symbol",
        "Find References",
        "Who Imports",
        "Imports Of",
        "Find Related Tests",
        "Find TODOs",
        "Hotspots",
        "Inspect Dependencies",
    ]
    assert all(
        spec.read_only and not spec.needs_network for spec in CATALOGUE if spec.name in TOOLS
    )
    assert not any(spec.needs_command_gate for spec in CATALOGUE if spec.name in TOOLS)


def test_find_symbol_lists_definitions_with_their_extent(box: Toolbox) -> None:
    result = box("Find Symbol", name="parse_config")

    lines = result.splitlines()
    assert lines[0] == "2 definition(s) matching 'parse_config'"  # and test_parse_config
    start = line_of("app/config.py", "def parse_config")
    assert lines[1] == f"app/config.py:{start} function parse_config (L{start}-L{start + 3})"


def test_find_symbol_matches_partially_filters_by_kind_and_scopes_to_a_path(box: Toolbox) -> None:
    both = box("Find Symbol", name="config")
    methods = box("Find Symbol", name="get", kind="method")
    scoped = box("Find Symbol", name="config", path="svc")

    assert "app/config.py" in both and "svc/server.go" in both  # ParseConfig, case-insensitive
    assert "method Client.get" in methods and "method Settings.get" in methods
    assert "function" not in methods
    assert "app/config.py" not in scoped and "svc/server.go" in scoped


def test_find_symbol_with_no_match_suggests_what_to_do(box: Toolbox) -> None:
    result = box("Find Symbol", name="nothing_like_it")

    assert result.startswith("No definition matching 'nothing_like_it'")
    assert "Search Project Files" in result


def test_show_symbol_prints_the_body_with_context_and_line_numbers(box: Toolbox) -> None:
    result = box("Show Symbol", name="parse_config", context=2)

    start = line_of("app/config.py", "def parse_config")
    assert result.splitlines()[0] == f"app/config.py:{start}-{start + 3} function parse_config"
    assert f"{start}\tdef parse_config(path=DEFAULT_PATH):" in result
    assert "TODO(ana)" in result
    assert f"{start - 2}\t" in result and f"{start + 3}\t" in result


def test_show_symbol_by_qualified_name_and_ambiguity(box: Toolbox) -> None:
    qualified = box("Show Symbol", name="Settings.get")
    ambiguous = box("Show Symbol", name="get")
    chosen = box("Show Symbol", name="get", file="web/src/api.ts")

    assert "method Settings.get" in qualified and "return self.data.get(key)" in qualified
    assert "2 definitions" in ambiguous and "pass file=" in ambiguous
    assert "method Client.get" in chosen and "fetchUser(path)" in chosen


def test_show_symbol_errors_name_the_closest_symbols(box: Toolbox) -> None:
    result = box("Show Symbol", name="parse_confg")

    assert result.startswith("ERROR:") and "parse_config" in result


def test_find_references_counts_by_kind_and_groups_by_file(box: Toolbox) -> None:
    result = box("Find References", symbol="parse_config")

    lines = result.splitlines()
    assert (
        lines[0] == "6 reference(s) to 'parse_config' in 4 file(s): 1 definition, 2 import, 3 call"
    )
    assert "app/main.py" in lines
    assert "  L1 import: from app.config import parse_config" in lines
    assert (
        f"  L{line_of('app/cli.py', 'config.parse_config')} call: return config.parse_config()"
        in lines
    )
    assert "Who Imports" in lines[-1]


def test_find_references_limits_results_and_says_so(box: Toolbox) -> None:
    result = box("Find References", symbol="parse_config", max_results=2)

    assert "4 more" in result


def test_who_imports_answers_what_depends_on_a_file(box: Toolbox) -> None:
    result = box("Who Imports", target="app/config.py")

    lines = result.splitlines()
    assert lines[0] == "3 file(s) import app/config.py"
    assert "app/cli.py:1 imports app" in result and "app/main.py:1 imports app.config" in result
    assert "tests/test_config.py:1 imports app.config" in result


def test_who_imports_can_follow_dependents_transitively(box: Toolbox) -> None:
    result = box("Who Imports", target="app/config.py", depth=2)

    assert result.splitlines()[0] == "4 file(s) depend on app/config.py (3 direct, 1 indirect)"
    assert "tests/test_cli.py (via app/cli.py)" in result


def test_who_imports_accepts_a_module_name_and_rejects_unknown_targets(box: Toolbox) -> None:
    by_module = box("Who Imports", target="app.config")
    unknown = box("Who Imports", target="app/confg.py")

    assert by_module.startswith("3 file(s) import app/config.py")
    assert unknown.startswith("ERROR:") and "app/config.py" in unknown


def test_who_imports_says_when_nothing_imports_the_file(box: Toolbox) -> None:
    assert box("Who Imports", target="tests/test_cli.py").startswith(
        "No file imports tests/test_cli.py"
    )


def test_imports_of_separates_project_files_from_external_ones(box: Toolbox) -> None:
    result = box("Imports Of", path="cmd/main.go")

    assert result.splitlines()[0] == "cmd/main.go imports 1 project file(s) and 1 external"
    assert "svc/server.go  (example.com/svc/svc, line 6)" in result
    assert "external: fmt" in result


def test_find_related_tests_for_a_file_and_a_symbol(box: Toolbox) -> None:
    by_file = box("Find Related Tests", target="app/config.py")
    by_symbol = box("Find Related Tests", target="parse_config")
    none = box("Find Related Tests", target="app/main.py")

    assert by_file.splitlines()[0] == "1 test file(s) related to app/config.py"
    assert "tests/test_config.py  imports app/config.py; name matches" in by_file
    assert "tests/test_config.py  mentions parse_config" in by_symbol
    assert none.startswith("No related tests found for app/main.py")


def test_find_todos_without_git_lists_them_and_says_owner_and_age_are_unknown(
    box: Toolbox,
) -> None:
    result = box("Find TODOs")

    lines = result.splitlines()
    assert lines[0] == "3 item(s) in 3 file(s): 1 FIXME, 1 HACK, 1 TODO"
    assert (
        f"app/config.py:{line_of('app/config.py', 'TODO')} TODO(ana) validate the schema" in result
    )
    assert "FIXME handle errors" in result and "HACK temporary port override" in result
    assert "no Git history" in lines[-1]


def test_find_todos_filters_by_tag_and_path(box: Toolbox) -> None:
    only_fixme = box("Find TODOs", tags="fixme")
    scoped = box("Find TODOs", path="svc")

    assert only_fixme.splitlines()[0] == "1 item(s) in 1 file(s): 1 FIXME"
    assert "svc/server.go" in scoped and "app/config.py" not in scoped


def test_hotspots_without_git_degrade_to_a_plain_answer(box: Toolbox) -> None:
    result = box("Hotspots")

    assert result.startswith("No Git history: ")
    assert not result.startswith("ERROR:")


def test_hotspots_and_todo_blame_use_the_git_history(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["code_intel"])
    root = box.workspace.root
    init_repo(root)
    commit(
        root, {"hot.py": "def f(x):\n    if x:\n        return 1\n    return 0\n"}, "a", "Ana", 30
    )
    for step in range(3):
        commit(
            root,
            {"hot.py": f"# TODO: step {step}\ndef f(x):\n    if x:\n        return {step}\n"},
            "b",
            "Bo",
            3,
        )
    commit(root, {"cold.py": "X = 1\n"}, "c", "Ana", 2)

    hotspots = box("Hotspots", top=5)
    todos = box("Find TODOs")

    assert hotspots.splitlines()[1].startswith("1. hot.py  score ")
    assert "cold.py" in hotspots
    assert "hot.py:1 TODO step 2  (Bo, 3d)" in todos


def test_inspect_dependencies_reports_manifests(box: Toolbox) -> None:
    result = box("Inspect Dependencies")

    assert result.splitlines()[0] == "3 manifest(s), 9 declared dependencies"
    assert "web/package.json (npm): 3 dependencies" in result
    assert "  left-pad ^1.3.0" in result


def test_every_call_emits_a_tool_event_and_an_unknown_path_is_an_error(box: Toolbox) -> None:
    box("Find Symbol", name="Settings")
    error = box("Imports Of", path="missing.py")

    events = [e for e in read_events(box.ctx.run_dir / "events.jsonl") if e.type == "tool.call"]
    assert [(e.data["tool"], e.data["ok"]) for e in events] == [
        ("Find Symbol", True),
        ("Imports Of", False),
    ]
    assert error.startswith("ERROR:")


def test_an_agent_answers_who_breaks_if_parse_config_changes(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context()
    write_polyglot(ctx.workspace.root)
    llm = ScriptedLLM(
        [
            ToolCall("Find References", {"symbol": "parse_config"}),
            ToolCall("Who Imports", {"target": "app/config.py"}),
            ToolCall("Find Related Tests", {"target": "parse_config"}),
            "Changing parse_config affects main.py, cli.py, and tests/test_config.py.",
        ]
    )

    result = run_agent_task(ctx, llm, tools=build_tools(ctx, groups=["code_intel"]), max_iter=8)

    assert result.startswith("Changing parse_config affects")
    llm.assert_exhausted()
    assert "app/main.py" in llm.calls[1].prompt and "app/cli.py" in llm.calls[1].prompt
    assert "3 file(s) import app/config.py" in llm.calls[2].prompt
    assert "tests/test_config.py" in llm.calls[3].prompt
