"""Plugin tools: discovery on and off, validation, the design rules, an agent calling one."""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.extensions import plugin_loader
from engineering_team.extensions.plugin_loader import PluginError, PluginSet, load_plugins
from engineering_team.plugins import plugin_tool
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import Settings, load_settings
from engineering_team.team import TeamError, build_roster
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import build_tools

MakeContext = Callable[..., RunContext]
runner = CliRunner()

WORD_COUNT = '''\
from engineering_team.plugins import PluginContext, plugin_tool


@plugin_tool(name="Word Count", group="fs_read", read_only=True, summary="Count words in a file.")
def word_count(ctx: PluginContext, path: str) -> str:
    """Count the words in a project text file. Use it to size a document before you summarise it.
    path is relative to the project root. Example: path="README.md"."""
    return f"{len(ctx.resolve(path).read_text(encoding='utf-8').split())} words"
'''
DESCRIPTION = "Does a thing well. Use it when you need that thing; the argument names the target."


def write_plugin(name: str, code: str = WORD_COUNT) -> Path:
    path = Path.cwd() / ".engineering-team" / "tools" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def allowed(**overrides: Any) -> Settings:
    return load_settings(overrides={"allow_project_plugins": True, **overrides})


def tool_of(code: str) -> str:
    """A plugin file with one tool built from ``code`` (the decorator line and function)."""

    return "from engineering_team.plugins import plugin_tool, ToolError\n" + code


def test_project_plugins_are_off_unless_allowed_and_the_file_is_reported_not_loaded(
    make_context: MakeContext,
) -> None:
    write_plugin("word_count")

    found = load_plugins(load_settings())

    assert found.tools == () and found.notes == ()
    assert (
        found.skipped
        and "word_count.py" in found.skipped[0]
        and "allow_project_plugins" in found.skipped[0]
    )
    assert "Word Count" not in {t.name for t in build_tools(make_context())}


def test_allowed_project_plugins_register_after_the_builtins_and_warn_loudly(
    make_context: MakeContext,
) -> None:
    write_plugin("word_count")
    settings = allowed()

    found = load_plugins(settings)
    ctx = make_context(settings=settings)
    names = [t.name for t in build_tools(ctx)]

    (item,) = found.tools
    assert (item.tool.name, item.tool.group, item.tool.read_only) == ("Word Count", "fs_read", True)
    assert item.source == ".engineering-team/tools/word_count.py"
    assert any("code" in n and "outside the write scope" in n for n in found.notes)
    assert names[-1] == "Word Count"
    assert names[:-1] == [t.name for t in build_tools(make_context("plain"))]
    assert [t.name for t in build_tools(ctx, groups=["fs_read"])][-1] == "Word Count"
    events = [e for e in read_events(ctx.run_dir / "events.jsonl")]
    assert any(e.type == "extension.warning" for e in events)
    assert any(e.type == "plugins.loaded" for e in events)


def test_a_plugin_tool_follows_the_tool_design_rules_at_run_time(make_context: MakeContext) -> None:
    write_plugin("word_count")
    ctx = make_context(settings=allowed())
    ctx.workspace.write_file("notes.txt", "one two three")
    (tool,) = [t for t in build_tools(ctx) if t.name == "Word Count"]

    assert tool.run(path="notes.txt") == "3 words"
    assert tool.run(path="missing.txt").startswith("ERROR: Path does not exist")
    assert tool.run(path=".git/config").startswith("ERROR:")
    assert tool.run(path=".engineering-team/anything").startswith("ERROR:")
    calls = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "tool.call"]
    assert [(e.data["tool"], e.data["ok"]) for e in calls] == [
        ("Word Count", ok) for ok in (True, False, False, False)
    ]
    assert calls[0].data["args"] == {"path": "notes.txt"}
    assert len(tool.description.split()) <= 120 and "Count the words" in tool.description


def test_an_agent_on_a_scripted_model_can_call_a_plugin_tool(make_context: MakeContext) -> None:
    write_plugin("word_count")
    ctx = make_context(settings=allowed())
    ctx.workspace.write_file("notes.txt", "one two three four")
    llm = ScriptedLLM([ToolCall("Word Count", {"path": "notes.txt"}), "done"])

    run_agent_task(ctx, llm)

    llm.assert_exhausted()
    assert "4 words" in llm.calls[-1].prompt and "ERROR:" not in llm.calls[-1].prompt


def test_a_plugin_that_raises_gives_the_agent_an_error_not_a_crash(
    make_context: MakeContext,
) -> None:
    write_plugin(
        "broken",
        tool_of(
            f'''
@plugin_tool(name="Broken", group="fs_read", read_only=True)
def broken(value: int) -> str:
    """{DESCRIPTION}"""
    if value == 1:
        raise ToolError("value 1 is not allowed; use 2")
    return 1 / 0
'''
        ),
    )
    ctx = make_context(settings=allowed())
    (tool,) = [t for t in build_tools(ctx) if t.name == "Broken"]

    assert tool.run(value=1) == "ERROR: value 1 is not allowed; use 2"
    result = tool.run(value=2)
    assert result.startswith("ERROR: plugin tool failed (ZeroDivisionError")


def test_the_plugin_signature_hides_the_context_and_results_are_bounded(
    make_context: MakeContext,
) -> None:
    write_plugin(
        "big",
        tool_of(
            f'''
@plugin_tool(name="Big", group="fs_read", read_only=True)
def big(ctx, repeat: int = 40000) -> str:
    """{DESCRIPTION}"""
    return "x" * repeat
'''
        ),
    )
    ctx = make_context(settings=allowed())
    (tool,) = [t for t in build_tools(ctx) if t.name == "Big"]

    assert list(tool.args_schema.model_fields) == ["repeat"]
    assert "output truncated at 30000 characters" in tool.run()


def test_read_only_teammates_only_get_read_only_plugin_tools(make_context: MakeContext) -> None:
    write_plugin("word_count")
    write_plugin(
        "writer",
        tool_of(
            f'''
@plugin_tool(name="Stamp File", group="fs_write")
def stamp(ctx, path: str) -> str:
    """{DESCRIPTION}"""
    return "stamped"
'''
        ),
    )
    ctx = make_context(settings=allowed())

    everything = {t.name for t in build_tools(ctx)}
    read_only = {t.name for t in build_tools(ctx, read_only=True)}

    assert {"Word Count", "Stamp File"} <= everything
    assert "Word Count" in read_only and "Stamp File" not in read_only


def test_a_plugin_in_the_web_group_follows_the_web_switch(make_context: MakeContext) -> None:
    write_plugin(
        "lookup",
        tool_of(
            f'''
@plugin_tool(name="Lookup", group="web", read_only=True, needs_network=True)
def lookup(term: str) -> str:
    """{DESCRIPTION}"""
    return term
'''
        ),
    )

    off = {t.name for t in build_tools(make_context(settings=allowed()))}
    on = {
        t.name
        for t in build_tools(make_context("other", settings=allowed(**{"web.enabled": True})))
    }

    assert "Lookup" not in off and "Lookup" in on


# -- a group of your own, and the roster ---------------------------------------------------------


RELEASE = tool_of(
    f'''
@plugin_tool(name="Release Notes", group="release", read_only=True)
def notes(since: str) -> str:
    """{DESCRIPTION}"""
    return f"notes since {{since}}"
'''
)


def test_a_custom_group_is_granted_through_a_teammates_tool_groups(
    make_context: MakeContext,
) -> None:
    write_plugin("release", RELEASE)
    settings = allowed(
        **{
            "team.release_manager": {
                "role": "Release manager",
                "goal": "Prepare the release.",
                "backstory": "You keep a changelog.",
                "tool_groups": ["fs_read", "release"],
            }
        }
    )
    ctx = make_context(settings=settings)
    from engineering_team.pipeline.stages import stage_groups

    mine = [t.name for t in build_tools(ctx, groups=stage_groups(ctx, "release_manager"))]
    other = [t.name for t in build_tools(ctx, groups=stage_groups(ctx, "backend_engineer"))]

    assert "Release Notes" in mine and "Release Notes" not in other


def test_a_group_nobody_declared_is_rejected_and_a_declared_one_is_listed() -> None:
    settings = allowed(**{"team.backend_engineer": {"tool_groups": ["fs_read", "release"]}})

    with pytest.raises(TeamError, match=r"unknown tool group.*release"):
        build_roster(settings)
    write_plugin("release", RELEASE)
    assert "release" in build_roster(settings).get("backend_engineer").tool_groups


def test_a_broken_plugin_is_a_team_error_where_the_team_is_defined() -> None:
    write_plugin("bad", "raise RuntimeError('boom')\n")

    with pytest.raises(
        TeamError, match=r"bad.py failed to load \(RuntimeError: boom\).*plugins.disable"
    ):
        build_roster(allowed())


# -- validation ----------------------------------------------------------------------------------


def tool_code(
    name: str = "Fine Name", group: str = "fs_read", params: str = "a: str", doc: str = DESCRIPTION
) -> str:
    return (
        f'@plugin_tool(name="{name}", group="{group}")\n'
        f'def t({params}) -> str:\n    """{doc}"""\n    return "x"\n'
    )


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (tool_code(name="Read Project File"), "already used by another tool"),
        (tool_code(name="x"), "the name must be"),
        (tool_code(group="Bad Group"), "the group 'Bad Group'"),
        (tool_code(doc="Too short."), "description .* is 2 words"),
        (tool_code(params="a"), "parameter 'a' needs a type hint"),
        (tool_code(params="*a: str"), r"\*args and \*\*kwargs"),
        ('TOOLS = ["not a tool"]\n', "expected a tool made with @plugin_tool"),
    ],
)
def test_a_tool_that_breaks_the_rules_is_refused_with_the_fix(code: str, message: str) -> None:
    write_plugin("rules", tool_of(code))

    with pytest.raises(PluginError, match=message):
        load_plugins(allowed())


def test_two_plugins_cannot_share_a_tool_name() -> None:
    write_plugin("a", WORD_COUNT)
    write_plugin("b", WORD_COUNT)

    with pytest.raises(PluginError, match="Word Count.*already used"):
        load_plugins(allowed())


def test_a_file_that_does_not_parse_names_itself() -> None:
    write_plugin("typo", "def broken(:\n")

    with pytest.raises(PluginError, match=r"typo.py failed to load \(SyntaxError"):
        load_plugins(allowed())


def test_disabled_underscored_and_non_python_files_are_not_loaded() -> None:
    write_plugin("word_count")
    write_plugin("_helpers", "raise RuntimeError('never imported')")
    (Path.cwd() / ".engineering-team" / "tools" / "notes.txt").write_text("x", encoding="utf-8")

    assert load_plugins(allowed(**{"plugins.disable": ["word_count"]})).tools == ()
    assert [t.tool.name for t in load_plugins(allowed()).tools] == ["Word Count"]


def test_discovery_is_cached_and_follows_the_file(tmp_path: Path) -> None:
    path = write_plugin("word_count")
    settings = allowed()

    first = load_plugins(settings)
    assert load_plugins(settings) is first

    path.write_text(WORD_COUNT.replace("Word Count", "Count Words"), encoding="utf-8")
    assert [t.tool.name for t in load_plugins(settings).tools] == ["Count Words"]


# -- entry points --------------------------------------------------------------------------------


class FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name, self.value, self._target = name, f"fake_pkg:{name}", target

    def load(self) -> object:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


@plugin_tool(name="Installed Tool", group="knowledge", read_only=True)
def installed(topic: str) -> str:
    """Does a thing well. Use it when you need that thing; the argument names the target."""
    return topic


def test_entry_point_plugins_load_by_default_from_a_tool_a_list_or_a_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = plugin_tool(name="Second Tool", group="knowledge", read_only=True)(installed.func)
    module = types.ModuleType("fake_module")
    module.third = plugin_tool(name="Third Tool", group="knowledge", read_only=True)(installed.func)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        plugin_loader,
        "discover_entry_points",
        lambda: [
            FakeEntryPoint("one", installed),
            FakeEntryPoint("two", [other]),
            FakeEntryPoint("three", module),
        ],
    )

    found = load_plugins(load_settings())

    assert [(t.tool.name, t.source.split(" (")[0]) for t in found.tools] == [
        ("Installed Tool", "plugin entry point 'one'"),
        ("Second Tool", "plugin entry point 'two'"),
        ("Third Tool", "plugin entry point 'three'"),
    ]
    assert found.notes == ()  # an installed package is your own decision: no warning


def test_entry_points_can_be_turned_off_or_skipped_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_loader, "discover_entry_points", lambda: [FakeEntryPoint("one", installed)]
    )

    assert load_plugins(load_settings(overrides={"plugins.entry_points": False})).tools == ()
    assert load_plugins(load_settings(overrides={"plugins.disable": ["one"]})).tools == ()


def test_an_entry_point_that_cannot_load_is_an_error_with_the_way_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_loader,
        "discover_entry_points",
        lambda: [FakeEntryPoint("one", ImportError("no module x"))],
    )

    with pytest.raises(
        PluginError, match=r"'one'.*ImportError: no module x.*plugins.disable = \['one'\]"
    ):
        load_plugins(load_settings())


# -- the command ---------------------------------------------------------------------------------


def test_plugins_list_shows_tools_json_and_the_rows_for_tools_md() -> None:
    write_plugin("word_count")
    Path("engineering-team.toml").write_text("allow_project_plugins = true\n", encoding="utf-8")

    text = runner.invoke(app, ["plugins", "list"])
    as_json = runner.invoke(app, ["--json", "plugins", "list"])
    rows = runner.invoke(app, ["plugins", "list", "--markdown"])

    assert text.exit_code == 0 and "Word Count" in text.output and "fs_read" in text.output
    assert "allow_project_plugins is on" in " ".join(text.output.split())
    data = json.loads(as_json.stdout)
    assert data["tools"][0] | {"summary": ""} == {
        "name": "Word Count",
        "group": "fs_read",
        "read_only": True,
        "needs_network": False,
        "source": ".engineering-team/tools/word_count.py",
        "summary": "",
    }
    assert rows.output.strip() == (
        "| `Word Count` | `fs_read` | yes | no | no | Count words in a file. "
        "(plugin: .engineering-team/tools/word_count.py) |"
    )


def test_plugins_list_says_what_is_not_loaded_and_fails_clearly_on_a_broken_plugin() -> None:
    write_plugin("word_count")

    idle = runner.invoke(app, ["plugins", "list"])
    assert idle.exit_code == 0 and "No plugin tools are loaded" in idle.output
    assert "Not loaded: .engineering-team/tools/word_count.py" in " ".join(idle.output.split())

    write_plugin("bad", "raise RuntimeError('boom')")
    Path("engineering-team.toml").write_text("allow_project_plugins = true\n", encoding="utf-8")
    broken = runner.invoke(app, ["plugins", "list"])
    assert broken.exit_code == 2 and "boom" in broken.output


def test_an_empty_plugin_set_adds_nothing() -> None:
    assert PluginSet().specs() == () and PluginSet().groups == frozenset()


def test_a_file_that_appears_later_is_noticed_even_while_project_plugins_are_off() -> None:
    settings = load_settings()
    assert load_plugins(settings).skipped == ()

    write_plugin("word_count")

    assert "word_count.py" in load_plugins(settings).skipped[0]
