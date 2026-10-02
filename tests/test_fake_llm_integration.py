"""End-to-end agent runs on a ScriptedLLM: real Agent, real executor, real tools, no network."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.runtime.context import RunContext
from engineering_team.settings import load_settings
from engineering_team.testing import (
    ScriptedLLM,
    ScriptExhausted,
    ToolCall,
    Turn,
    build_agent,
    build_task,
    run_agent_task,
)
from engineering_team.tools import ProjectWorkspace, WriteScope

MakeContext = Callable[..., RunContext]

SCRIPT_PY = "print('answer', 6 * 7)\n"


@pytest.mark.parametrize("native_tools", [True, False], ids=["native", "react"])
def test_an_agent_writes_a_file_runs_it_and_reports_the_result(
    make_context: MakeContext, native_tools: bool
) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            ToolCall("Write Project File", {"path": "script.py", "content": SCRIPT_PY}),
            ToolCall("Run Project Command", {"command": "python script.py"}),
            "The script prints answer 42.",
        ],
        native_tools=native_tools,
    )

    result = run_agent_task(ctx, llm)

    assert "answer 42" in result
    assert (ctx.workspace.root / "script.py").read_text(encoding="utf-8") == SCRIPT_PY
    llm.assert_exhausted()
    # The command output was fed back to the model on the final turn.
    assert "answer 42" in llm.calls[-1].prompt
    assert len(llm.calls) == 3


def test_the_model_sees_the_task_and_the_tool_catalogue(make_context: MakeContext) -> None:
    llm = ScriptedLLM(["done"])

    run_agent_task(make_context(), llm, "Build the habit tracker backend.")

    first = llm.calls[0]
    assert "Build the habit tracker backend." in first.prompt
    assert first.tools is not None
    names = {tool["function"]["name"] for tool in first.tools}
    assert {"write_project_file", "run_project_command"} <= names


def test_a_tool_error_reaches_the_model_and_the_agent_recovers(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            ToolCall("Read Project File", {"path": "missing.txt"}),
            lambda messages, tools: (
                "recovered" if "ERROR:" in str(messages[-1]["content"]) else "no error seen"
            ),
        ]
    )

    assert run_agent_task(ctx, llm) == "recovered"


def test_write_scope_blocks_an_agent_from_changing_files_it_does_not_own(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            ToolCall("Write Project File", {"path": "src/web/page.tsx", "content": "x"}),
            ToolCall("Write Project File", {"path": "src/api/handler.py", "content": "ok\n"}),
            "done",
        ]
    )

    run_agent_task(ctx, llm, write_scope=WriteScope(allow=("src/api/**",)))

    assert not (ctx.workspace.root / "src/web/page.tsx").exists()
    assert (ctx.workspace.root / "src/api/handler.py").is_file()
    assert "outside your write scope" in llm.calls[1].prompt


def test_cancelling_the_run_stops_tool_work(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            ToolCall("Write Project File", {"path": "first.txt", "content": "1"}),
            lambda messages, tools: (
                ctx.cancel_event.set()
                or ToolCall("Write Project File", {"path": "second.txt", "content": "2"})
            ),
            ToolCall("Write Project File", {"path": "third.txt", "content": "3"}),
            "stopping",
        ]
    )

    run_agent_task(ctx, llm)

    assert (ctx.workspace.root / "first.txt").is_file()
    assert not (ctx.workspace.root / "second.txt").exists()
    assert not (ctx.workspace.root / "third.txt").exists()
    assert "cancelled" in llm.calls[-1].prompt


def test_the_iteration_limit_forces_a_final_answer(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM([ToolCall("List Project Files", {})] * 3 + ["forced summary", "unused"])

    result = run_agent_task(ctx, llm, max_iter=3)

    assert "forced summary" in result
    # Three tool turns, then the executor asks for a final answer instead of looping on.
    assert len(llm.calls) == 4
    assert llm.remaining == 1


def test_running_out_of_script_fails_loudly(make_context: MakeContext) -> None:
    llm = ScriptedLLM([ToolCall("List Project Files", {})])

    with pytest.raises(ScriptExhausted, match="no response for call #2"):
        run_agent_task(make_context(), llm)


def test_unused_script_items_are_reported(make_context: MakeContext) -> None:
    llm = ScriptedLLM(["done", "never used"])

    run_agent_task(make_context(), llm)

    with pytest.raises(AssertionError, match="1 scripted response"):
        llm.assert_exhausted()


def test_token_usage_is_scripted_and_accumulated(make_context: MakeContext) -> None:
    llm = ScriptedLLM(
        [
            Turn(ToolCall("List Project Files", {}), prompt_tokens=100, completion_tokens=20),
            Turn("done", prompt_tokens=150, completion_tokens=30),
        ]
    )

    run_agent_task(make_context(), llm)

    usage = llm.get_token_usage_summary()
    assert (usage.prompt_tokens, usage.completion_tokens) == (250, 50)
    assert (usage.total_tokens, usage.successful_requests) == (300, 2)


def test_parallel_tool_calls_in_one_turn_all_run(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            [
                ToolCall("Write Project File", {"path": "a.txt", "content": "a"}),
                ToolCall("Write Project File", {"path": "b.txt", "content": "b"}),
            ],
            "done",
        ]
    )

    run_agent_task(ctx, llm)

    assert {p.name for p in ctx.workspace.root.glob("*.txt")} == {"a.txt", "b.txt"}


def test_two_agents_on_two_contexts_run_concurrently_without_crosstalk(
    make_context: MakeContext,
) -> None:
    results: dict[str, str] = {}

    def work(name: str) -> None:
        ctx = make_context(name)
        llm = ScriptedLLM(
            [ToolCall("Write Project File", {"path": "who.txt", "content": name}), f"{name} done"]
        )
        agent = build_agent(ctx, llm)
        results[name] = agent.execute_task(build_task(agent))

    threads = [threading.Thread(target=work, args=(name,)) for name in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert results == {"alpha": "alpha done", "beta": "beta done"}


def test_an_agent_searches_reads_a_range_and_patches(tmp_path: Path) -> None:
    """The navigate-then-edit loop the toolbelt exists for, driven through the real agent."""

    workspace = ProjectWorkspace.create(tmp_path / "project")
    workspace.write_file(
        "src/billing.py",
        "def total(items):\n    return sum(i.price for i in items)\n\n\n"
        "def tax(amount):\n    return amount * 0.2\n" + "# padding\n" * 300,
    )
    workspace.write_file("src/other.py", "def unrelated():\n    return 1\n")
    ctx = RunContext.create(load_settings(), workspace)  # the baseline includes both files
    patch = (
        "--- a/src/billing.py\n+++ b/src/billing.py\n@@ -5,2 +5,2 @@\n def tax(amount):\n"
        "-    return amount * 0.2\n+    return round(amount * 0.2, 2)\n"
    )
    llm = ScriptedLLM(
        [
            ToolCall("Search Project Files", {"pattern": "def tax"}),
            ToolCall(
                "Read File Range", {"path": "src/billing.py", "start_line": 5, "max_lines": 2}
            ),
            ToolCall("Apply Patch", {"patch": patch}),
            ToolCall("Workspace Changes", {}),
            "tax now rounds to cents",
        ]
    )

    result = run_agent_task(ctx, llm)

    assert result == "tax now rounds to cents"
    assert "round(amount * 0.2, 2)" in (ctx.workspace.root / "src/billing.py").read_text()
    prompts = [call.prompt for call in llm.calls]
    assert "src/billing.py:5: def tax(amount):" in prompts[1]  # the search hit reached the model
    assert (
        "5\tdef tax(amount):" in prompts[2] and "padding" not in prompts[2]
    )  # a range, not the file
    assert "Applied changes to 1 file(s)" in prompts[3]
    assert "M src/billing.py" in prompts[4]
    llm.assert_exhausted()
