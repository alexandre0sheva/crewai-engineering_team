"""Running one stage: a small crew (one agent, one task) built from ``agents.yaml`` and
``stages.yaml``.

The pipeline talks to a :class:`StageRunner`; :class:`CrewStageRunner` is the real one (tests use
``ScriptedLLM`` through its ``llm_factory``, or a fake runner). A stage that must hand a
contract to the next stage asks CrewAI for ``output_pydantic`` and fails clearly when the
agent's answer is not a valid contract.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from typing import Any, Protocol, cast

import yaml
from crewai import Agent, Crew, CrewOutput, Process, Task
from pydantic import BaseModel

from engineering_team.contracts import Contract, Plan, Spec, WorkPackage
from engineering_team.crew import build_llm
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.tools import WriteScope, build_tools
from engineering_team.tools.browser_tools import available as browser_tools_available

# Tool groups every stage agent gets for now (a later task assigns groups per teammate). The
# ``web`` group exists only when web access is enabled; ``browser`` is added below for the
# teammates who check the UI and only when the optional extra is installed.
STAGE_GROUPS = (
    "fs_read",
    "fs_write",
    "search",
    "command",
    "dev",
    "runtime",
    "code_intel",
    "git_read",
    "board",
    "notes",
    "human",
    "web",
)
BROWSER_TEAMMATES = frozenset({"frontend_engineer", "quality_engineer", "generalist_engineer"})

# Contracts a stage can produce, by output name.
CONTRACT_MODELS: dict[str, type[Contract]] = {"spec": Spec, "plan": Plan}


class StageError(RuntimeError):
    """A stage whose agent did not deliver what the recipe asked for."""


@dataclass(frozen=True)
class StageRequest:
    """What a runner needs to run one stage (or one work package of a parallel stage)."""

    ctx: RunContext
    stage: StageSpec
    teammate: str
    state: PipelineState  # a copy: runners read it, only the pipeline changes the real one
    requirements: str = ""
    card_id: str | None = None
    steering: str = ""  # notes from the person running the team, delivered once
    note: str = ""  # resume or retry instruction for the agent
    package: WorkPackage | None = None
    lane: int | str | None = None  # the parallel lane this unit works in
    write_scope: WriteScope | None = None  # the paths its agent may change (None: any)
    failures: str = ""  # a repair stage: the checks the controller found failing

    @property
    def label(self) -> str:
        return f"{self.stage.name}-{self.package.id}" if self.package else self.stage.name


@dataclass
class StageOutput:
    """What a stage produced: its contracts by output name, and the agent's own summary."""

    contracts: dict[str, Contract] = field(default_factory=dict)
    summary: str = ""


class StageRunner(Protocol):
    def run(self, request: StageRequest) -> StageOutput: ...


def stage_groups(ctx: RunContext, teammate: str) -> tuple[str, ...]:
    groups: tuple[str, ...] = STAGE_GROUPS
    if teammate in BROWSER_TEAMMATES and browser_tools_available():
        groups = (*groups, "browser")
    return groups


@functools.cache
def _yaml(name: str) -> dict[str, Any]:
    text = (resources.files("engineering_team") / "config" / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def teammate_for(stage: StageSpec, package: WorkPackage | None) -> str:
    """The teammate who works on the stage (or on this work package of it)."""

    if package is None:
        return stage.teammates[0]
    role = package.role.strip().lower().replace(" ", "_")
    for teammate in stage.teammates:
        if role in (teammate, teammate.removesuffix("_engineer")):
            return teammate
    for teammate in stage.teammates:
        if role[:4] and teammate.startswith(role[:4]):
            return teammate
    return stage.teammates[0]


def _json(model: BaseModel | None) -> str:
    return model.model_dump_json(indent=2) if model is not None else "(not available)"


class CrewStageRunner:
    """Runs each stage as its own small crew: one agent, one task, the real tools."""

    def __init__(self, llm_factory: Callable[[str], Any] | None = None) -> None:
        self._llm_factory = llm_factory

    def run(self, request: StageRequest) -> StageOutput:
        ctx, stage = request.ctx, request.stage
        prompts = _yaml("stages.yaml")
        key = {"parallel": "implement", "verify": "repair"}.get(stage.kind, stage.name)
        if key not in prompts:
            raise StageError(
                f"No prompt for stage '{stage.name}' in config/stages.yaml (known: "
                f"{', '.join(prompts)}). Add one with that name."
            )
        prompt = prompts[key]
        agent = self._agent(request)
        wanted = {name: CONTRACT_MODELS[name] for name in stage.contract_outputs}
        if len(wanted) > 1:
            raise StageError(
                f"Stage '{stage.name}' asks for several contracts ({', '.join(wanted)}); "
                "an agent stage can return one."
            )
        task = Task(
            description=prompt["description"],
            expected_output=prompt["expected_output"],
            agent=agent,
            output_pydantic=next(iter(wanted.values()), None),
        )
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            cache=False,
            memory=False,
            planning=False,
            verbose=ctx.settings.verbose,
            tracing=ctx.settings.tracing,
            share_crew=False,
            # Controller-owned state: agents cannot reach this path through the file tools.
            output_log_file=str(ctx.run_dir / f"crew-log-{request.label}.json"),
        )
        result = crew.kickoff(inputs=self._inputs(request))
        if not isinstance(result, CrewOutput):  # a streaming crew; this one never streams
            raise StageError(f"Stage '{stage.name}' produced no output.")
        output = StageOutput(summary=str(result.raw or "").strip())
        for name, model in wanted.items():
            if result.pydantic is None or not isinstance(result.pydantic, model):
                raise StageError(
                    f"Stage '{stage.name}' did not return a valid {model.__name__} "
                    f"(got: {output.summary[:300]!r}). Answer with the JSON the task asks for."
                )
            output.contracts[name] = result.pydantic
        return output

    def _agent(self, request: StageRequest) -> Agent:
        ctx, teammate = request.ctx, request.teammate
        configs = _yaml("agents.yaml")
        if teammate not in configs:
            raise StageError(
                f"Unknown teammate '{teammate}'; agents.yaml defines: {', '.join(configs)}."
            )
        resolved = ctx.settings.resolve_model(teammate)
        llm = (
            self._llm_factory(teammate) if self._llm_factory else build_llm(resolved, ctx.settings)
        )
        agent = Agent(
            config=configs[teammate],
            llm=llm,
            tools=build_tools(
                ctx,
                groups=stage_groups(ctx, teammate),
                write_scope=request.write_scope,
                agent=teammate,
                lane=request.lane,
            ),
            mcps=ctx.settings.docs_mcp_urls or None if ctx.settings.docs_mcp_enabled else None,
            allow_delegation=False,
            max_iter=resolved.max_iter,
            verbose=ctx.settings.verbose,
            inject_date=True,
        )
        if ctx.llm_rate is not None:
            # One limiter for every agent of the run, so parallel lanes share max_rpm.
            agent.set_rpm_controller(cast(Any, ctx.llm_rate))
        return agent

    @staticmethod
    def _inputs(request: StageRequest) -> dict[str, Any]:
        ctx = request.ctx
        card = (
            f"Your task board card is {request.card_id}. Use the Task Board tool to comment, "
            "add subtasks, or flag a blocker; the controller decides when work is done."
            if request.card_id
            else ""
        )
        notes = (
            f"Notes from the person running the team:\n{request.steering}"
            if request.steering
            else ""
        )
        package = request.package.model_dump(mode="json") if request.package else None
        return {
            "project_name": ctx.settings.project_name,
            "requirements": request.requirements,
            "workspace_path": str(ctx.workspace.root),
            "current_date": date.today().isoformat(),
            "spec": _json(request.state.spec),
            "plan": _json(request.state.plan),
            "package": json.dumps(package, indent=2) if package else "",
            "packages": json.dumps(
                {pid: p.model_dump(mode="json") for pid, p in request.state.packages.items()},
                indent=2,
            ),
            "card": card,
            "notes": notes,
            "resume_note": request.note,
            "failures": request.failures,
        }
