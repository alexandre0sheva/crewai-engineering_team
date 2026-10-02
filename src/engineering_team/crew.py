"""Crew definition for the universal MVP engineering team."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.lite_agent_output import LiteAgentOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.task_output import TaskOutput

from engineering_team.model_routing import REASONING_EFFORT_PREFIXES, ResolvedModel
from engineering_team.settings import Settings, load_settings
from engineering_team.tools.workspace_tools import get_workspace, workspace_tools

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


MIN_ARTIFACT_CHARACTERS = 40


def _require_workspace_files(
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
        workspace = get_workspace()
        problems = []
        for relative_path in required_paths:
            path = workspace.resolve(relative_path)
            if not path.is_file():
                problems.append(f"{relative_path} (missing)")
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if len("".join(content.split())) < MIN_ARTIFACT_CHARACTERS:
                problems.append(f"{relative_path} (nearly empty)")
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
    """A lead-managed, stack-agnostic engineering crew for MVP delivery."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def engineering_lead(self) -> Agent:
        """Create the tool-free custom manager required by hierarchical crews."""

        resolved = self.settings.resolve_model("engineering_lead")
        return Agent(
            config=self.agents_config["engineering_lead"],  # type: ignore[index]
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
        resolved = self.settings.resolve_model(config_name)
        return Agent(
            config=self.agents_config[config_name],  # type: ignore[index]
            llm=build_llm(resolved, self.settings),
            tools=workspace_tools,
            mcps=self.settings.docs_mcp_urls or None if self.settings.docs_mcp_enabled else None,
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
                "docs/architecture.md",
                "docs/implementation-plan.md",
            ),
            guardrail_max_retries=2,
        )

    @task
    def foundation_task(self) -> Task:
        return Task(
            config=self.tasks_config["foundation_task"],  # type: ignore[index]
            guardrail=_require_workspace_files("README.md"),
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
            guardrail=_require_workspace_files("docs/verification.md"),
            guardrail_max_retries=2,
        )

    @task
    def release_task(self) -> Task:
        return Task(
            config=self.tasks_config["release_task"],  # type: ignore[index]
            guardrail=_require_workspace_files("docs/release-report.md"),
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """Build a hierarchical crew whose manager delegates and validates."""

        workspace = get_workspace()
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
            output_log_file=str(workspace.root / ".engineering-team" / "crew-log.json"),
        )
