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
from crewai.tools import BaseTool
from pydantic import BaseModel

from engineering_team.contracts import Contract, Plan, ReviewReport, Spec, WorkPackage
from engineering_team.crew import build_llm
from engineering_team.intake.context_docs import context_note
from engineering_team.modes.codebase_map import (
    ChunkAnalysis,
    CodebaseMap,
    map_context,
)
from engineering_team.modes.fix_contracts import FixNote, Repro, Triage
from engineering_team.modes.fix_input import bug_brief
from engineering_team.modes.maintain import coverage_brief
from engineering_team.modes.maintain_contracts import UpgradePlan
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.team import Roster, TeamError
from engineering_team.tools import WriteScope, build_tools

# Contracts a stage can produce, by output name.
CONTRACT_MODELS: dict[str, type[Contract]] = {
    "spec": Spec,
    "plan": Plan,
    "triage": Triage,
    "repro": Repro,
    "fix_note": FixNote,
    "upgrades": UpgradePlan,
}


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
    # Teammates work packages may go to (the plan stage lists them).
    roles: tuple[str, ...] = ()
    findings: str = ""  # a repair stage: the review findings the controller wants fixed
    tools: tuple[BaseTool, ...] | None = None  # prebuilt (read-only job); None: build
    chunk: str = ""  # an analyze stage: the brief of the part of the codebase to study
    synthesis: str = ""  # an analyze stage's last step: the chunk analyses to combine
    profile: str = ""  # an analyze stage: the facts the controller found about the repository
    upgrades: str = ""  # an upgrade stage: the group of upgrades to apply, as text
    audit: str = ""  # what the controller's dependency audit found, as text

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
    """The teammate's tool groups. One the machine or setup cannot provide (browser extra not
    installed, web disabled) is left out by ``build_tools``, never an error mid-run; `doctor`
    says what is missing."""

    return ctx.team.get(teammate).groups


@functools.cache
def _yaml(name: str) -> dict[str, Any]:
    text = (resources.files("engineering_team") / "config" / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def prompts() -> dict[str, Any]:
    """The task prompts of ``config/stages.yaml`` by key."""

    return _yaml("stages.yaml")


def prompt_key(stage: StageSpec, *, synthesis: bool = False, findings: bool = False) -> str:
    """The key in ``stages.yaml`` of the task prompt ``stage`` runs: its own ``prompt``, a recipe's
    ``instructions`` (the ``custom`` prompt), or what its kind or name picks. An analyze stage's
    last step and a verify stage's repair of review findings have prompts of their own."""

    if stage.instructions is not None:
        return "custom"
    key = stage.prompt or {
        "parallel": "implement", "verify": "repair", "analyze": "analyze_chunk",
        "upgrade": "upgrade_apply", "review": "review",
    }.get(stage.kind, stage.name)  # fmt: skip
    if stage.kind == "analyze" and synthesis:
        key = "analyze_synthesis"  # the last step combines what the chunk analysts found
    if stage.kind == "verify" and findings:
        key = "repair_review"  # a repair round for review findings, not for failing checks
    return key


def teammate_for(
    stage: StageSpec, package: WorkPackage | None, roster: Roster | None = None
) -> str:
    """The teammate who works on the stage (or on this work package of it).

    A package's ``role`` names a teammate (``backend``, ``frontend_engineer``, or the key of a
    custom teammate that lists the stage under ``stages``). With a ``roster`` the choice is
    limited to enabled teammates and a missing or disabled one falls back to the nearest enabled
    generalist (see ``Roster.assign``).
    """

    if package is None:
        return _lead(stage, roster)
    role = package.role.strip().lower().replace(" ", "_")
    pool = package_pool(stage, roster)
    for teammate in pool:
        if role in (teammate, teammate.removesuffix("_engineer")):
            return teammate
    for teammate in pool:
        if role[:4] and teammate.startswith(role[:4]):
            return teammate
    return _lead(stage, roster, pool)


def package_pool(stage: StageSpec, roster: Roster | None) -> list[str]:
    """Teammates a work package of ``stage`` can be given to: the stage's own, plus enabled
    teammates that list the stage under ``stages`` (that is how a custom teammate joins in)."""

    pool = list(stage.teammates)
    if roster is not None:
        pool += [m.key for m in roster.enabled() if stage.name in m.stages and m.key not in pool]
        pool = [key for key in pool if roster.usable(key)] or pool
    return pool


def _lead(stage: StageSpec, roster: Roster | None, pool: list[str] | None = None) -> str:
    preferred = pool or list(stage.teammates)
    if roster is None:
        return preferred[0]
    return roster.assign(preferred, stage.name)[0]


def lead_teammate(ctx: RunContext, stage: StageSpec) -> str:
    """Who works ``stage`` itself (its first usable teammate), reporting a fallback once."""

    wanted = stage.teammates[0]
    key, why = ctx.team.assign(stage.teammates, stage.name)
    if why and ctx.team.note_fallback(stage.name, wanted, key):
        ctx.events.emit("team.fallback", stage=stage.name, wanted=wanted, used=key, why=why)
    return key


def _context(request: StageRequest) -> str:
    """The project context a prompt carries: the reference documents the user supplied, and, for
    an adopted project, the codebase map (size-capped). The analysts that write the map do not
    get it: they must read the code as it is now."""

    ctx = request.ctx
    note = context_note(ctx.workspace.root)
    if request.stage.kind == "analyze":
        return note
    mapped = map_context(ctx.workspace.root, ctx.settings.analysis.context_chars)
    if not mapped:
        return note
    heading = (
        "Codebase map of the existing project, written by analysts who read it (a guide, not "
        "evidence: check anything you rely on in the code):"
    )
    return f"{note}\n\n{heading}\n\n{mapped}".strip()


def stage_instructions(request: StageRequest) -> str:
    return (request.stage.instructions or "").strip()


def _json(model: BaseModel | None) -> str:
    return model.model_dump_json(indent=2) if model is not None else "(not available)"


class CrewStageRunner:
    """Runs each stage as its own small crew: one agent, one task, the real tools."""

    def __init__(self, llm_factory: Callable[[str], Any] | None = None) -> None:
        self._llm_factory = llm_factory

    def run(self, request: StageRequest) -> StageOutput:
        ctx, stage = request.ctx, request.stage
        known = prompts()
        key = prompt_key(stage, synthesis=bool(request.synthesis), findings=bool(request.findings))
        if key not in known:
            raise StageError(
                f"No prompt for stage '{stage.name}' in config/stages.yaml (known: "
                f"{', '.join(known)}). Add one with that name."
            )
        prompt = known[key]
        agent = self._agent(request)
        wanted: dict[str, type[Contract]] = {
            name: CONTRACT_MODELS[name] for name in stage.contract_outputs
        }
        if stage.kind == "review":  # each reviewer returns its own report; the controller merges
            wanted = {"review": ReviewReport}
        if stage.kind == "analyze":  # chunk analyses and the final map; the controller writes it
            wanted = {"analysis": CodebaseMap if request.synthesis else ChunkAnalysis}
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
        try:
            member = ctx.team.get(teammate)
        except TeamError as exc:
            raise StageError(str(exc)) from exc
        resolved = ctx.settings.resolve_model(teammate, tier=member.tier, max_iter=member.max_iter)
        llm = (
            self._llm_factory(teammate) if self._llm_factory else build_llm(resolved, ctx.settings)
        )
        agent = Agent(
            role=member.role,
            goal=member.goal,
            backstory=member.backstory,
            llm=llm,
            tools=list(request.tools)
            if request.tools is not None
            else build_tools(
                ctx,
                groups=stage_groups(ctx, teammate),
                write_scope=request.write_scope,
                agent=teammate,
                lane=request.lane,
            ),
            mcps=ctx.settings.docs_mcp_urls or None
            if member.uses_docs_mcp and ctx.settings.docs_mcp_enabled
            else None,
            allow_delegation=False,  # a stage crew has one agent: nobody to delegate to
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
            "instructions": stage_instructions(request),
            "triage": _json(request.state.triage),
            "repro": _json(request.state.repro),
            "fix_note": _json(request.state.fix_note),
            "bug": bug_brief(ctx.run_dir, ctx.workspace.root, request.state),
            "package": json.dumps(package, indent=2) if package else "",
            "packages": json.dumps(
                {pid: p.model_dump(mode="json") for pid, p in request.state.packages.items()},
                indent=2,
            ),
            "teammates": "\n".join(
                f"- {key}: {ctx.team.members[key].role_for(ctx.settings.project_name)}"
                for key in request.roles
                if key in ctx.team.members
            ),
            "context": _context(request),
            "card": card,
            "notes": notes,
            "resume_note": request.note,
            "failures": request.failures,
            "findings": request.findings,
            "base": str((request.state.isolation or {}).get("base_commit") or "")[:12],
            "chunk": request.chunk,
            "synthesis": request.synthesis,
            "profile": request.profile,
            "upgrades": request.upgrades,
            "audit": request.audit,
            "coverage": coverage_brief(request.state),
        }
