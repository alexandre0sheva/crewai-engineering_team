"""Crew definition for the universal MVP engineering team."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.lite_agent_output import LiteAgentOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.task_output import TaskOutput

from engineering_team.artifacts import missing_artifacts
from engineering_team.model_routing import REASONING_EFFORT_PREFIXES, ResolvedModel
from engineering_team.runtime.context import RunContext
from engineering_team.settings import Settings
from engineering_team.tools import PROJECT_GROUPS, ProjectWorkspace, build_tools

# CrewAI's own default context-window headroom (it uses 75% of a model's window).
CONTEXT_WINDOW_USAGE_RATIO = 0.75


def build_llm(resolved: ResolvedModel, settings: Settings) -> LLM:
    """Construct a CrewAI LLM from a resolved model, sending only parameters it accepts."""

    options: dict[str, Any] = {"model": resolved.model}
    prefix = resolved.provider_prefix
    if resolved.reasoning_effort is not None and prefix in REASONING_EFFORT_PREFIXES:
        options["reasoning_effort"] = resolved.reasoning_effort
    if resolved.temperature is not None:
        options["temperature"] = resolved.temperature
    if resolved.api is not None and prefix == "openai":
        options["api"] = resolved.api
    if resolved.context_window is not None:
        options["context_window_size"] = int(resolved.context_window * CONTEXT_WINDOW_USAGE_RATIO)
    if prefix == "ollama":
        options["base_url"] = settings.ollama_base_url
    return LLM(**options)


def _require_workspace_files(
    workspace: ProjectWorkspace,
    *required_paths: str,
) -> Callable[[TaskOutput | LiteAgentOutput], tuple[bool, Any]]:
    """Interim guardrail: required artifacts must exist and hold real content.

    This only rejects absent or near-empty files; it does not prove the work is correct.
    Independent, controller-run verification replaces it later in 0.2.0.
    """

    # CrewAI 1.15.23 accepts a real ``tuple[bool, Any]`` return annotation but rejects the
    # stringified form this module produces via ``from __future__ import annotations``.
    # Keep the closure itself unannotated; the factory's return type documents the contract.
    def validate(output: TaskOutput | LiteAgentOutput):
        problems = missing_artifacts(workspace, *required_paths)
        if problems:
            return (
                False,
                "Required project artifacts are missing or incomplete: "
                + ", ".join(problems)
                + ". Create or complete them with the project filesystem tools, "
                "then summarize the result.",
            )
        return True, output.raw

    return validate


@CrewBase
class EngineeringTeam:
    """A lead-managed, stack-agnostic engineering crew for MVP delivery.

    This is the ``hierarchical`` strategy: four fixed specialists and six fixed tasks, which is
    what ``@CrewBase`` is for. The pipeline needs a roster that changes per project, so it builds
    its agents from ``ctx.team`` instead (``pipeline/stages.py``). Here the roster supplies each
    teammate's prompt, tier and iteration limit (and so ``[team.<key>]`` changes to those apply),
    while the tools stay ``PROJECT_GROUPS`` and ``enabled`` is not consulted: the tasks need
    those four specialists.
    """

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.settings = ctx.settings

    def engineering_lead(self) -> Agent:
        """Create the tool-free custom manager required by hierarchical crews."""

        member = self.ctx.team.get("engineering_lead")
        resolved = self.settings.resolve_model(
            "engineering_lead", tier=member.tier, max_iter=member.max_iter
        )
        return Agent(
            role=member.role,
            goal=member.goal,
            backstory=member.backstory,
            llm=build_llm(resolved, self.settings),
            # CrewAI 1.15 rejects custom hierarchical managers that are
            # constructed with project or MCP tools. During task execution it
            # supplies the lead with scoped delegation/coworker tools instead.
            allow_delegation=True,
            max_iter=resolved.max_iter,
            verbose=self.settings.verbose,
            inject_date=True,
        )

    def _specialist(self, config_name: str) -> Agent:
        member = self.ctx.team.get(config_name)
        resolved = self.settings.resolve_model(
            config_name, tier=member.tier, max_iter=member.max_iter
        )
        return Agent(
            role=member.role,
            goal=member.goal,
            backstory=member.backstory,
            llm=build_llm(resolved, self.settings),
            tools=build_tools(self.ctx, groups=PROJECT_GROUPS),
            mcps=self.settings.docs_mcp_urls or None
            if member.uses_docs_mcp and self.settings.docs_mcp_enabled
            else None,
            allow_delegation=False,
            max_iter=resolved.max_iter,
            verbose=self.settings.verbose,
            inject_date=True,
        )

    @agent
    def solution_architect(self) -> Agent:
        return self._specialist("solution_architect")

    @agent
    def backend_engineer(self) -> Agent:
        return self._specialist("backend_engineer")

    @agent
    def frontend_engineer(self) -> Agent:
        return self._specialist("frontend_engineer")

    @agent
    def quality_engineer(self) -> Agent:
        return self._specialist("quality_engineer")

    @task
    def architecture_task(self) -> Task:
        return Task(
            config=self.tasks_config["architecture_task"],  # type: ignore[index]
            guardrail=_require_workspace_files(
                self.ctx.workspace,
                "docs/architecture.md",
                "docs/implementation-plan.md",
            ),
            guardrail_max_retries=2,
        )

    @task
    def foundation_task(self) -> Task:
        return Task(
            config=self.tasks_config["foundation_task"],  # type: ignore[index]
            guardrail=_require_workspace_files(self.ctx.workspace, "README.md"),
            guardrail_max_retries=2,
        )

    @task
    def backend_task(self) -> Task:
        return Task(config=self.tasks_config["backend_task"])  # type: ignore[index]

    @task
    def frontend_task(self) -> Task:
        return Task(config=self.tasks_config["frontend_task"])  # type: ignore[index]

    @task
    def quality_task(self) -> Task:
        return Task(
            config=self.tasks_config["quality_task"],  # type: ignore[index]
            guardrail=_require_workspace_files(self.ctx.workspace, "docs/verification.md"),
            guardrail_max_retries=2,
        )

    @task
    def release_task(self) -> Task:
        return Task(
            config=self.tasks_config["release_task"],  # type: ignore[index]
            guardrail=_require_workspace_files(self.ctx.workspace, "docs/release-report.md"),
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """Build a hierarchical crew whose manager delegates and validates."""

        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            manager_agent=self.engineering_lead(),
            process=Process.hierarchical,
            cache=False,
            memory=False,
            planning=False,
            verbose=self.settings.verbose,
            tracing=self.settings.tracing,
            share_crew=False,
            # Controller-owned state: agents cannot reach this path through the file tools.
            output_log_file=str(self.ctx.run_dir / "crew-log.json"),
        )
