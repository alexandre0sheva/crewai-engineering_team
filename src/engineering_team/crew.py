"""Crew definition for the universal MVP engineering team."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.task_output import TaskOutput

from engineering_team.tools.workspace_tools import get_workspace, workspace_tools

STANDARD_PROFILE = "standard"
SMOKE_PROFILE = "smoke"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run_profile() -> str:
    return os.getenv("ENGINEERING_RUN_PROFILE", STANDARD_PROFILE).strip().lower()


def _profile_setting(
    standard_environment_name: str,
    smoke_environment_name: str,
    standard_default: str,
    smoke_default: str,
) -> str:
    if _run_profile() == SMOKE_PROFILE:
        return os.getenv(smoke_environment_name, smoke_default)
    return os.getenv(standard_environment_name, standard_default)


def _make_llm(
    standard_model_environment: str,
    smoke_model_environment: str,
    standard_model: str,
    smoke_model: str,
    standard_effort_environment: str,
    smoke_effort_environment: str,
    standard_effort: str,
    smoke_effort: str,
) -> LLM:
    return LLM(
        model=_profile_setting(
            standard_model_environment,
            smoke_model_environment,
            standard_model,
            smoke_model,
        ),
        reasoning_effort=_profile_setting(  # type: ignore[arg-type]
            standard_effort_environment,
            smoke_effort_environment,
            standard_effort,
            smoke_effort,
        ),
    )


def _max_iter(
    standard_environment_name: str,
    smoke_environment_name: str,
    standard: int,
    smoke: int,
) -> int:
    return int(
        _profile_setting(
            standard_environment_name,
            smoke_environment_name,
            str(standard),
            str(smoke),
        )
    )


def _optional_docs_mcps() -> list[str] | None:
    if _run_profile() == SMOKE_PROFILE:
        return None
    configured = os.getenv("ENGINEERING_DOCS_MCP_URLS", "")
    urls = [url.strip() for url in configured.split(",") if url.strip()]
    return urls or None


def _require_workspace_files(*required_paths: str) -> Callable[[TaskOutput], tuple[bool, Any]]:
    # CrewAI 1.15.9 accepts an unannotated guardrail return, but rejects the
    # modern ``tuple[bool, Any]`` spelling while looking specifically for
    # ``typing.Tuple``. Keep the closure itself unannotated until that validator
    # accepts PEP 585 annotations.
    def validate(output: TaskOutput):
        missing = [
            relative_path
            for relative_path in required_paths
            if not get_workspace().resolve(relative_path).is_file()
        ]
        if missing:
            return (
                False,
                "Required project artifacts are missing: "
                + ", ".join(missing)
                + ". Create them with the project filesystem tools, then summarize the result.",
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

    def engineering_lead(self) -> Agent:
        """Create the tool-free custom manager required by hierarchical crews."""

        return Agent(
            config=self.agents_config["engineering_lead"],  # type: ignore[index]
            llm=_make_llm(
                "ENGINEERING_LEAD_MODEL",
                "ENGINEERING_SMOKE_LEAD_MODEL",
                "openai/gpt-5.6-sol",
                "openai/gpt-5.6-terra",
                "ENGINEERING_LEAD_REASONING_EFFORT",
                "ENGINEERING_SMOKE_LEAD_REASONING_EFFORT",
                "high",
                "low",
            ),
            # CrewAI 1.15.9 rejects custom hierarchical managers that are
            # constructed with project or MCP tools. During task execution it
            # supplies the lead with scoped delegation/coworker tools instead.
            allow_delegation=True,
            max_iter=_max_iter(
                "ENGINEERING_LEAD_MAX_ITER",
                "ENGINEERING_SMOKE_LEAD_MAX_ITER",
                standard=35,
                smoke=18,
            ),
            verbose=_env_bool("ENGINEERING_VERBOSE", True),
            inject_date=True,
        )

    def _specialist(self, config_name: str) -> Agent:
        return Agent(
            config=self.agents_config[config_name],  # type: ignore[index]
            llm=_make_llm(
                "ENGINEERING_WORKER_MODEL",
                "ENGINEERING_SMOKE_WORKER_MODEL",
                "openai/gpt-5.6-terra",
                "openai/gpt-5.6-luna",
                "ENGINEERING_WORKER_REASONING_EFFORT",
                "ENGINEERING_SMOKE_WORKER_REASONING_EFFORT",
                "low",
                "none",
            ),
            tools=workspace_tools,
            mcps=_optional_docs_mcps(),
            allow_delegation=False,
            max_iter=_max_iter(
                "ENGINEERING_WORKER_MAX_ITER",
                "ENGINEERING_SMOKE_WORKER_MAX_ITER",
                standard=30,
                smoke=14,
            ),
            verbose=_env_bool("ENGINEERING_VERBOSE", True),
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
            verbose=_env_bool("ENGINEERING_VERBOSE", True),
            tracing=_env_bool("ENGINEERING_TRACING", False),
            share_crew=False,
            output_log_file=str(workspace.resolve(".engineering-team/crew-log.json")),
        )
