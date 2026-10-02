"""Helpers that wire a real CrewAI ``Agent`` and ``Task`` to a ``ScriptedLLM`` and real tools."""

from __future__ import annotations

from crewai import Agent, Task
from crewai.tools import BaseTool

from engineering_team.runtime.context import RunContext
from engineering_team.testing.fake_llm import ScriptedLLM
from engineering_team.tools import WriteScope, build_tools


def build_agent(
    ctx: RunContext,
    llm: ScriptedLLM,
    *,
    tools: list[BaseTool] | None = None,
    write_scope: WriteScope | None = None,
    read_only: bool = False,
    max_iter: int = 10,
    role: str = "Test Engineer",
) -> Agent:
    """A real agent on ``llm`` whose tools (by default the real ones) are bound to ``ctx``."""

    return Agent(
        role=role,
        goal="Complete the assigned task using the project tools.",
        backstory="A deterministic test double of an engineer.",
        llm=llm,
        tools=tools
        if tools is not None
        else build_tools(ctx, write_scope=write_scope, read_only=read_only),
        max_iter=max_iter,
        max_retry_limit=0,  # a scripting mistake should fail at once, not be retried
        allow_delegation=False,
        verbose=False,
    )


def build_task(
    agent: Agent,
    description: str = "Do the work and report the result.",
    expected_output: str = "A short summary of what was done.",
) -> Task:
    return Task(description=description, expected_output=expected_output, agent=agent)


def run_agent_task(
    ctx: RunContext,
    llm: ScriptedLLM,
    description: str = "Do the work and report the result.",
    **agent_options: object,
) -> str:
    """Run one agent on one task end to end and return its final answer text."""

    agent = build_agent(ctx, llm, **agent_options)  # type: ignore[arg-type]
    return agent.execute_task(build_task(agent, description))
