from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from crewai.tools import BaseTool

from engineering_team.runtime.context import RunContext
from engineering_team.settings import load_settings
from engineering_team.tools import CATALOGUE, WriteScope, browser_tools, build_tools

MakeContext = Callable[..., RunContext]


@pytest.fixture(autouse=True)
def browser_extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_tools, "available", lambda: True)


def _by_name(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {tool.name: tool for tool in tools}


def test_the_default_toolset_covers_every_catalogued_tool_but_the_opt_in_web_group(
    make_context: MakeContext,
) -> None:
    tools = build_tools(make_context())

    assert [tool.name for tool in tools] == [spec.name for spec in CATALOGUE if spec.group != "web"]
    assert {"Read Project File", "Write Project File", "Run Project Command"} <= {
        tool.name for tool in tools
    }


def test_tool_results_are_never_cached(make_context: MakeContext) -> None:
    for tool in build_tools(make_context()):
        assert tool.cache_function(None, None) is False


def test_tools_are_closed_over_their_own_context(make_context: MakeContext) -> None:
    first, second = make_context("first"), make_context("second")
    first_tools, second_tools = build_tools(first), build_tools(second)

    assert not set(map(id, first_tools)) & set(map(id, second_tools))
    _by_name(first_tools)["Write Project File"].run(path="only-first.txt", content="1")

    assert (first.workspace.root / "only-first.txt").is_file()
    assert not (second.workspace.root / "only-first.txt").exists()
    assert "only-first.txt" not in _by_name(second_tools)["List Project Files"].run()


def test_two_runs_in_two_threads_never_see_each_others_files(make_context: MakeContext) -> None:
    barrier = threading.Barrier(2)
    seen: dict[str, tuple[str, str]] = {}

    def work(name: str) -> None:
        tools = _by_name(build_tools(make_context(name)))
        tools["Write Project File"].run(path="shared-name.txt", content=f"written by {name}")
        barrier.wait(timeout=10)  # both runs have written before either reads
        seen[name] = (
            tools["Read Project File"].run(path="shared-name.txt"),
            tools["List Project Files"].run(),
        )

    threads = [threading.Thread(target=work, args=(name,)) for name in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert seen["alpha"][0] == "written by alpha"
    assert seen["beta"][0] == "written by beta"
    assert all("shared-name.txt" in listing for _content, listing in seen.values())


def test_errors_are_returned_as_error_strings(make_context: MakeContext) -> None:
    tools = _by_name(build_tools(make_context()))

    assert tools["Read Project File"].run(path="missing.txt").startswith("ERROR:")
    assert tools["Write Project File"].run(path="../escape.txt", content="x").startswith("ERROR:")


def test_read_only_tools_exclude_everything_that_changes_state(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("notes.txt", "hello")

    tools = _by_name(build_tools(ctx, read_only=True))

    assert set(tools) == {spec.name for spec in CATALOGUE if spec.read_only and spec.group != "web"}
    assert {"Read Project File", "Search Project Files", "Workspace Changes"} <= set(tools)
    assert not {"Write Project File", "Apply Patch", "Run Project Command"} & set(tools)
    assert tools["Read Project File"].run(path="notes.txt") == "hello"


def test_write_scope_allows_owned_paths_and_blocks_the_rest(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("src/web/page.tsx", "page")
    tools = _by_name(build_tools(ctx, write_scope=WriteScope(allow=("src/api/**",))))
    write, replace = tools["Write Project File"], tools["Replace In Project File"]

    assert write.run(path="src/api/handler.py", content="x = 1\n").startswith("Wrote")
    assert replace.run(path="src/api/handler.py", old_text="1", new_text="2").startswith("Wrote")

    denied = write.run(path="src/web/other.tsx", content="nope")
    assert denied.startswith("ERROR:") and "src/api/**" in denied
    assert replace.run(path="src/web/page.tsx", old_text="page", new_text="x").startswith("ERROR:")
    assert tools["Delete Project Path"].run(path="src/web/page.tsx").startswith("ERROR:")
    assert not (ctx.workspace.root / "src/web/other.tsx").exists()
    assert ctx.workspace.read_file("src/web/page.tsx") == "page"
    # Reads are unrestricted.
    assert tools["Read Project File"].run(path="src/web/page.tsx") == "page"


def test_scope_is_checked_after_symlink_resolution(make_context: MakeContext) -> None:
    ctx = make_context()
    (ctx.workspace.root / "src" / "web").mkdir(parents=True)
    (ctx.workspace.root / "src" / "api").mkdir(parents=True)
    (ctx.workspace.root / "src" / "api" / "alias").symlink_to("../web", target_is_directory=True)
    write = _by_name(build_tools(ctx, write_scope=WriteScope(allow=("src/api/**",))))[
        "Write Project File"
    ]

    assert write.run(path="src/api/alias/sneaky.txt", content="x").startswith("ERROR:")
    assert not (ctx.workspace.root / "src" / "web" / "sneaky.txt").exists()


def test_deleting_a_directory_requires_every_file_in_it_to_be_in_scope(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("src/api/a.py", "a")
    ctx.workspace.write_file("src/api/generated/b.py", "b")
    scope = WriteScope(allow=("src/api/**",), deny=("src/api/generated/**",))
    delete = _by_name(build_tools(ctx, write_scope=scope))["Delete Project Path"]

    assert delete.run(path="src/api").startswith("ERROR:")
    assert (ctx.workspace.root / "src/api/generated/b.py").exists()
    assert delete.run(path="src/api/a.py").startswith("Deleted")


def test_a_cancelled_run_stops_accepting_tool_calls(make_context: MakeContext) -> None:
    ctx = make_context()
    tools = _by_name(build_tools(ctx))
    ctx.cancel_event.set()

    result = tools["Write Project File"].run(path="late.txt", content="x")

    assert result.startswith("ERROR:") and "cancelled" in result
    assert not (ctx.workspace.root / "late.txt").exists()


def test_commands_run_in_the_context_workspace(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("check.py", "print('hello from the project')\n")

    result = _by_name(build_tools(ctx))["Run Project Command"].run(command="python check.py")

    assert "Exit code: 0" in result and "hello from the project" in result


def test_the_command_gate_limits_concurrent_commands(make_context: MakeContext) -> None:
    ctx = make_context(settings=load_settings(overrides={"execution.max_parallel_commands": 1}))
    ctx.workspace.write_file("check.py", "print('done')\n")
    run = _by_name(build_tools(ctx))["Run Project Command"]
    results: list[str] = []
    ctx.command_gate.acquire()  # another command already occupies the only slot

    thread = threading.Thread(target=lambda: results.append(run.run(command="python check.py")))
    thread.start()
    thread.join(timeout=0.5)
    assert thread.is_alive() and not results

    ctx.command_gate.release()
    thread.join(timeout=30)
    assert results and "done" in results[0]


def test_the_package_exposes_factories_not_module_level_tool_objects() -> None:
    import engineering_team.tools as tools_package

    assert sorted(tools_package.__all__) == [
        "CATALOGUE",
        "GROUPS",
        "PROJECT_GROUPS",
        "ProjectWorkspace",
        "ToolSpec",
        "WorkspaceError",
        "WriteScope",
        "build_tools",
    ]


@pytest.mark.parametrize("name", ["registry", "workspace", "commands", "scope", "support"])
def test_tool_modules_are_importable_on_their_own(name: str) -> None:
    import importlib

    assert importlib.import_module(f"engineering_team.tools.{name}")
