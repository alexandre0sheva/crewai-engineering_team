"""The tool-schema diet: lazy tools held back behind Find Tool and Use Tool."""

from __future__ import annotations

import json
from collections.abc import Callable

from crewai.utilities.agent_utils import convert_tools_to_openai_schema

from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import load_settings
from engineering_team.team.registry import builtin_roster
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import CATALOGUE, EAGER_TOOLS, FINDER_TOOLS, build_tools

MakeContext = Callable[..., RunContext]
SCHEMA_TOKENS = 6_000  # the plan's target per agent
CHARS_PER_TOKEN = 4


def schema_tokens(tools: list) -> int:  # type: ignore[type-arg]
    schemas, _, _ = convert_tools_to_openai_schema(tools)
    return len(json.dumps(schemas)) // CHARS_PER_TOKEN


def test_lazy_tools_are_held_back_behind_find_tool_and_use_tool(make_context: MakeContext) -> None:
    ctx = make_context()

    names = [tool.name for tool in build_tools(ctx)]

    assert names[-2:] == list(FINDER_TOOLS)
    assert set(names[:-2]) == {
        spec.name for spec in CATALOGUE if not spec.lazy and spec.group != "web"
    }
    assert set(names[:-2]) <= EAGER_TOOLS
    assert "Git History Search" not in names


def test_lazy_false_or_the_setting_gives_every_tool_as_its_own(make_context: MakeContext) -> None:
    eager = make_context("eager", settings=load_settings(overrides={"tools.lazy": False}))

    assert not set(FINDER_TOOLS) & {tool.name for tool in build_tools(eager)}
    assert not set(FINDER_TOOLS) & {tool.name for tool in build_tools(make_context(), lazy=False)}


def test_a_group_without_lazy_tools_gets_no_finder(make_context: MakeContext) -> None:
    names = [tool.name for tool in build_tools(make_context(), groups=["fs_read"], read_only=True)]

    assert "Find Tool" in names  # File Info and Project Tree are held back
    assert [tool.name for tool in build_tools(make_context("h"), groups=["human"])] == ["Ask Human"]


def test_every_built_in_teammate_sends_under_the_schema_target(make_context: MakeContext) -> None:
    ctx = make_context(settings=load_settings(overrides={"web.enabled": True}))
    roster = builtin_roster()

    sizes = {
        member.key: schema_tokens(build_tools(ctx, groups=member.groups, agent=member.key))
        for member in roster.all()
    }

    assert max(sizes.values()) <= SCHEMA_TOKENS, sizes
    everything = schema_tokens(build_tools(ctx, lazy=False))
    assert everything > 2 * max(sizes.values())  # what an agent with every group paid before


def test_find_tool_lists_matching_tools_with_their_arguments(make_context: MakeContext) -> None:
    ctx = make_context()
    find = {tool.name: tool for tool in build_tools(ctx)}["Find Tool"]

    found = find.run(query="git history")

    assert found.startswith("Call these with Use Tool(name, arguments):")
    assert found.splitlines()[1].startswith("Git History Search:")
    assert 'arguments: pattern (string) = "", mode (string) = "string"' in found
    assert "Git History Search" in find.description  # the agent knows the names up front
    assert find.run(query="").count("\n  arguments:") == 8  # everything, a page at a time
    assert find.run(query="zzzz").startswith("ERROR: No held-back tool matches 'zzzz'.")


def test_use_tool_calls_a_held_back_tool_which_reports_its_own_call(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("src/app.py", "VALUE = 1\n")
    use = {tool.name: tool for tool in build_tools(ctx)}["Use Tool"]

    result = use.run(name="file info", arguments={"path": "src/app.py"})

    assert "src/app.py" in result and not result.startswith("ERROR")
    calls = [
        e.data["tool"] for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "tool.call"
    ]
    assert calls == ["File Info"]


def test_use_tool_errors_say_how_to_fix_the_call(make_context: MakeContext) -> None:
    use = {tool.name: tool for tool in build_tools(make_context())}["Use Tool"]

    unknown = use.run(name="Read Project File", arguments={"path": "x"})
    invalid = use.run(name="File Info", arguments={"nope": 1})

    assert unknown.startswith("ERROR: 'Read Project File' is not a held-back tool.")
    assert "called by their own name" in unknown
    assert invalid.startswith("ERROR: File Info cannot take these arguments")
    assert "Its arguments: path (string)" in invalid


def test_an_agent_finds_and_uses_a_held_back_tool(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("README.md", "# Notes\n")
    llm = ScriptedLLM(
        [
            ToolCall("Find Tool", {"query": "file info"}),
            ToolCall("Use Tool", {"name": "File Info", "arguments": {"path": "README.md"}}),
            "README.md is a small text file.",
        ]
    )

    result = run_agent_task(ctx, llm)

    llm.assert_exhausted()
    assert result == "README.md is a small text file."
    assert "File Info:" in llm.calls[1].prompt
    assert "README.md" in llm.calls[2].prompt and "lines" in llm.calls[2].prompt


def test_use_tool_takes_arguments_beside_the_name_or_as_json_text(
    make_context: MakeContext,
) -> None:
    # The first live run put the card id next to ``name``; it was dropped and the call failed.
    ctx = make_context()
    ctx.workspace.write_file("src/app.py", "VALUE = 1\n")
    use = {tool.name: tool for tool in build_tools(ctx)}["Use Tool"]

    beside = use.run(name="File Info", path="src/app.py")
    as_text = use.run(name="File Info", arguments='{"path": "src/app.py"}')
    garbled = use.run(name="File Info", arguments="path=src/app.py")

    assert "src/app.py" in beside and not beside.startswith("ERROR")
    assert as_text == beside
    assert garbled.startswith("ERROR: arguments must be an object")


def test_an_agent_may_put_the_arguments_beside_the_name(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("README.md", "# Notes\n")
    llm = ScriptedLLM([ToolCall("Use Tool", {"name": "File Info", "path": "README.md"}), "Done."])

    run_agent_task(ctx, llm)

    llm.assert_exhausted()
    assert "README.md" in llm.calls[1].prompt and "ERROR" not in llm.calls[1].prompt
