"""An agent on a ScriptedLLM can call each core file, search, and script tool.

The plan requires every tool to be proven callable by an agent, not just by a unit test. These
tools are exercised one run per group, and each result must reach the model without an error.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

import pytest

from engineering_team.runtime.context import RunContext
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task

MakeContext = Callable[..., RunContext]


def _project(ctx: RunContext) -> None:
    ctx.workspace.write_file("pkg/app.py", "def handler():\n    return 'ok'\n\nCONSTANT = 1\n")
    ctx.workspace.write_file("pkg/util.py", "from pkg.app import handler\n\nhandler()\n")
    ctx.workspace.write_file("Makefile", "hello:\n\t@echo from-make\n")


def test_an_agent_navigates_with_read_many_info_find_outline_and_repo_map(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    _project(ctx)
    llm = ScriptedLLM(
        [
            ToolCall("Read Many Files", {"files": ["pkg/app.py", "pkg/util.py"]}),
            ToolCall("File Info", {"path": "pkg/app.py"}),
            ToolCall("Find Files", {"pattern": "*.py"}),
            ToolCall("Project Outline", {"path": "pkg"}),
            ToolCall("Repo Map", {}),
            ToolCall("Project Tree", {}),
            "done",
        ]
    )

    run_agent_task(ctx, llm)

    llm.assert_exhausted()
    seen = llm.calls[-1].prompt
    assert "def handler" in seen  # Read Many Files
    assert "pkg/util.py" in seen and "handler" in seen  # Find Files, Project Outline, Repo Map
    assert "ERROR:" not in seen


def test_an_agent_changes_the_tree_with_mkdir_copy_move_and_delete(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    _project(ctx)
    llm = ScriptedLLM(
        [
            ToolCall("Make Directory", {"path": "build/out"}),
            ToolCall("Copy Path", {"source": "pkg/app.py", "destination": "build/out/app.py"}),
            ToolCall("Move Path", {"source": "pkg/util.py", "destination": "pkg/helpers.py"}),
            ToolCall("Delete Project Path", {"path": "Makefile"}),
            "done",
        ]
    )

    run_agent_task(ctx, llm)

    root = ctx.workspace.root
    assert (root / "build/out/app.py").is_file() and (root / "pkg/app.py").is_file()
    assert (root / "pkg/helpers.py").is_file() and not (root / "pkg/util.py").exists()
    assert not (root / "Makefile").exists()
    assert "ERROR:" not in llm.calls[-1].prompt


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_an_agent_lists_and_runs_a_project_script(make_context: MakeContext) -> None:
    ctx = make_context(extra_commands=["make"])
    _project(ctx)
    llm = ScriptedLLM(
        [
            ToolCall("List Scripts", {}),
            ToolCall("Run Script", {"name": "hello"}),
            "done",
        ]
    )

    run_agent_task(ctx, llm)

    llm.assert_exhausted()
    seen = llm.calls[-1].prompt
    assert "hello" in seen
    assert "from-make" in seen and "ERROR:" not in seen
