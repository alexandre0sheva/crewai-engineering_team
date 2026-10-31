"""The planning half of ``do``: triage the request, compose the recipe, say what it will cost.

Nothing here changes a project or spends more than the one cheap triage call. ``plan_request``
turns a request (and the directory it is about, if any) into a :class:`DoPlan`: the task profile,
the route the work takes, the recipe, where the escalation ladder starts and what a run is
estimated to cost. ``render_plan`` shows it and ``plan_document`` is its JSON form (also kept in
the run directory as ``task_profile.json``). ``do_command.py`` asks the person and runs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from engineering_team.bench.estimate import per_run_cost
from engineering_team.contracts import TaskProfile
from engineering_team.intake.signals import Signals, collect
from engineering_team.intake.triage import (
    TriageResult,
    build_triage_llm,
    signal_lines,
    triage,
)
from engineering_team.intake.triage_rules import MODE_INTENTS
from engineering_team.pipeline.compose import compose
from engineering_team.pipeline.compose_rules import ComposeError
from engineering_team.pipeline.ladder import RUNGS, SIZE_SCALE, estimate_usd, money, plan_ladder
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.lean import assess
from engineering_team.pipeline.recipes import Recipe, load_recipe
from engineering_team.pipeline.strategies import RECIPE_STRATEGIES
from engineering_team.settings import Settings
from engineering_team.team import build_roster

# Where the work goes: a new project, a change to an existing one, a read-only review of a
# branch, or the codebase map (the answer to a question about a project, for now).
Route = Literal["new", "repository", "review", "analyze", "answer"]
MODES = tuple(MODE_INTENTS)
# The maintenance presets (``maintain --task``) a profile's primary intent stands for.
TASK_OF: dict[str, str] = {
    "tests": "add-tests", "refactor": "refactor", "upgrade_deps": "upgrade-deps", "docs": "docs",
    "security_audit": "security-audit",
}  # fmt: skip
ESTIMATE_AS = {"single": "single", "pipeline": "pipeline", "team": "team"}


@dataclass(frozen=True)
class PlanStage:
    name: str
    kind: str
    why: str
    cost: str = ""


@dataclass
class DoPlan:
    request: str
    root: Path | None  # the existing project (None: a new project in the workspace root)
    triage: TriageResult
    route: Route
    strategy: str
    recipe: Recipe | None = None  # None: the single-agent baseline, or a route with its own flow
    stages: list[PlanStage] = field(default_factory=list)
    ladder: LadderRecord | None = None
    size: str = "small"  # the size the controller sizes the run with (``lean``)
    first_usd: float | None = None
    worst_usd: float | None = None
    budget_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def profile(self) -> TaskProfile:
        return self.triage.profile

    @property
    def run_mode(self) -> str:
        """The run's mode (what the manifest and the teammates' ``modes`` filters see)."""

        recipe = self.recipe
        return (recipe.mode if recipe is not None else None) or "maintain"


def check_mode(mode: str | None, signals: Signals) -> None:
    """A forced ``--mode`` that cannot work here is a usage error (``ValueError``)."""

    if mode is None:
        return
    if mode not in MODES:
        raise ValueError(f"--mode {mode!r} is not one of {', '.join(MODES)}.")
    if mode == "new" and signals.existing_code:
        raise ValueError(
            f"--mode new makes a new project, but {signals.repo} already has code. Run it "
            "elsewhere, or use --mode feature to add to this project."
        )
    if mode != "new" and not signals.existing_code:
        where = signals.repo or "here"
        raise ValueError(
            f"--mode {mode} works on an existing project and {where} has no project files. "
            "Run it inside a Git repository or pass --repo PATH."
        )


def route_of(profile: TaskProfile, mode: str | None = None) -> Route:
    if profile.primary in ("question", "research", "plan_only") and mode != "analyze":
        return "answer"  # read-only, whether or not there is a project
    if profile.target.kind == "new":
        return "new"
    if mode == "analyze":
        return "analyze"
    if profile.primary == "review":
        return "review"
    return "repository"


def run_triage(
    request: str, signals: Signals, settings: Settings, mode: str | None, *, use_model: bool
) -> tuple[TriageResult, list[str]]:
    """The triage of ``request``: one cheap model call, or the signals alone when ``use_model``
    is false or no model can be called (the second value says why)."""

    notes: list[str] = []
    llm: Any = None
    if use_model:
        missing = settings.missing_credentials()
        if missing:
            notes.append(f"no model call for triage ({missing[0]}); the signals decided.")
        else:
            try:
                llm = build_triage_llm(settings)
            except Exception as exc:  # a provider setup problem must not stop a preview
                notes.append(f"no model call for triage ({exc}); the signals decided.")
    return triage(request, signals, llm=llm, mode=mode, settings=settings), notes


def plan_request(
    request: str,
    root: Path | None,
    settings: Settings,
    *,
    mode: str | None = None,
    recipe_name: str | None = None,
    use_model: bool = True,
    answers: Any = None,
) -> DoPlan:
    """Triage ``request`` and plan its run. ``answers`` is a callable ``(TriageResult) ->
    TaskProfile`` that settles the questions of a profile that needs clarification.

    Raises ``ValueError`` (a usage error) when no plan can run: a forced mode that does not fit,
    a recipe that does not exist or no stage that fits the request.
    """

    signals = collect(request, root)
    check_mode(mode, signals)
    result, notes = run_triage(request, signals, settings, mode, use_model=use_model)
    if answers is not None and result.profile.needs_clarification:
        result = TriageResult(answers(result), result.signals, result.usage, result.error)
    profile = result.profile
    route = route_of(profile, mode)
    plan = DoPlan(
        request=request,
        root=root if profile.target.kind == "existing" else None,
        triage=result,
        route=route,
        strategy=settings.strategy,
        budget_usd=settings.budget.max_cost_usd,
        notes=notes,
    )
    teammates = set(build_roster(settings).members)
    try:
        if recipe_name or route == "analyze":  # --mode analyze is the bundled map recipe
            recipe = load_recipe(recipe_name or "adopt", plan.root, teammates=teammates)
            plan.recipe = recipe
            plan.stages = [
                PlanStage(s.name, s.kind, s.why or "From your recipe.") for s in recipe.stages
            ]
        else:
            composition = compose(profile, teammates=teammates)
            plan.recipe = composition.recipe
            plan.stages = [
                PlanStage(c.name, spec.kind, c.why, c.cost)
                for c, spec in zip(composition.stages, composition.recipe.stages, strict=True)
            ]
    except ComposeError as exc:
        raise ValueError(str(exc)) from exc
    if route in ("new", "repository"):
        _estimate(plan, settings)
    return plan


def _estimate(plan: DoPlan, settings: Settings) -> None:
    """Where the run starts and what it is estimated to cost (priors, an order of magnitude)."""

    existing_files = plan.triage.signals.repo_files if plan.root is not None else 0
    lean = assess(plan.request, settings, existing_files)
    plan.size = lean.size
    if plan.strategy not in RECIPE_STRATEGIES and plan.route == "new":
        plan.recipe = None  # one agent builds the project; there are no stages to compose
        plan.stages = [
            PlanStage("build", "agent", f"One agent with every tool (--strategy {plan.strategy}).")
        ]
    if plan.strategy == "auto" and plan.recipe is not None:
        plan.ladder = plan_ladder(settings, lean, plan.recipe)
        rungs = RUNGS[RUNGS.index(plan.ladder.start) : RUNGS.index(plan.ladder.max_rung) + 1]
        costs = [estimate_usd(settings, rung, lean.size) for rung in rungs]
        plan.first_usd = costs[0]
        plan.worst_usd = None if None in costs else sum(c for c in costs if c is not None)
        return
    kind = ESTIMATE_AS.get(plan.strategy, "pipeline")
    plan.first_usd = plan.worst_usd = per_run_cost(settings, kind, SIZE_SCALE[lean.size])[0]


def with_notes(request: str, profile: TaskProfile) -> str:
    """The request the team gets: what was asked, then the answers the person gave and the
    assumptions triage made (both are context for the team, not instructions)."""

    parts: list[str] = []
    if profile.clarifications:
        parts += ["Answers to clarifying questions:", *(f"- {c}" for c in profile.clarifications)]
    if profile.assumptions:
        parts += [
            "Assumptions triage made (not confirmed):",
            *(f"- {a}" for a in profile.assumptions),
        ]
    if not parts:
        return request
    return (
        f"{request.rstrip()}\n\n---\nNotes from triage (context, not instructions):\n"
        + "\n".join(parts)
    )


# -- showing the plan --------------------------------------------------------------------------


def render_plan(plan: DoPlan) -> list[str]:
    """The preview: what the request is understood to be, the stages and why, and the cost."""

    profile = plan.profile
    where = f"the project at {plan.root}" if plan.root else "a new project"
    how = "signals only" if profile.source == "signals" else "model"
    lines = [
        f"Understood as: {', '.join(profile.intents)} on {where}",
        f"  size {profile.size}, risk {profile.risk}, confidence {profile.confidence} ({how})",
    ]
    if profile.rationale:
        lines.append(f"  {profile.rationale}")
    if profile.verify_by:
        lines.append(f"  proved by: {', '.join(profile.verify_by)}")
    lines += [f"  assumption: {a}" for a in profile.assumptions]
    lines += [f"  you said: {c}" for c in profile.clarifications]
    lines += [f"  rule: {o}" for o in profile.overrides]
    lines += [f"Note: {note}" for note in plan.notes]
    lines.append(_route_line(plan))
    for index, stage in enumerate(plan.stages, 1):
        cost = f", {stage.cost}" if stage.cost else ""
        lines += [f"  {index:2}. {stage.name} [{stage.kind}{cost}]", f"      {stage.why}"]
    lines.append(_cost_line(plan))
    usage = plan.triage.usage
    if usage is not None:
        lines.append(
            f"Triage: {usage.model}, {usage.prompt_tokens + usage.completion_tokens:,} tokens, "
            f"{money(usage.cost_usd)}"
        )
    return lines


def _route_line(plan: DoPlan) -> str:
    if plan.route == "review":
        return "Plan: a read-only review of your change (nothing is modified)."
    if plan.route == "analyze":
        return "Plan: profile the project and write its codebase map (the project is not changed)."
    if plan.route == "answer":
        read = "" if plan.root else " from the web (needs --allow-web)"
        if plan.profile.primary == "plan_only":
            return "Plan: write a plan, read-only; nothing is implemented. Stages:"
        return f"Plan: answer read-only{read}; the controller checks every citation. Stages:"
    ladder = plan.ladder
    if ladder is not None:
        climb = "" if ladder.max_rung == ladder.start else f", climbing to {ladder.max_rung}"
        head = f"Plan: strategy auto, starting on the {ladder.start} rung{climb} ({plan.size} task)"
        reasons = "; ".join(ladder.reasons)
        recipe = plan.recipe.name if plan.recipe else ""
        return f"{head}\n  {reasons}\n  stages of the recipe {recipe}:"
    name = plan.recipe.name if plan.recipe is not None else "one agent"
    return f"Plan: strategy {plan.strategy}, recipe {name} ({plan.size} task); stages:"


def _cost_line(plan: DoPlan) -> str:
    if plan.route in ("review", "analyze", "answer"):
        return "Cost: not estimated (read-only)."
    first, worst = money(plan.first_usd), money(plan.worst_usd)
    span = f"about {first}" if first == worst else f"about {first}, up to {worst} if it climbs"
    budget = (
        f"; the budget stops the run at ${plan.budget_usd:.2f}"
        if plan.budget_usd is not None
        else "; no cost limit set (budget.max_cost_usd)"
    )
    return f"Estimated cost: {span} (a rough prior, within 0.5x-2x){budget}."


def plan_document(plan: DoPlan) -> dict[str, Any]:
    """The plan as JSON-ready data."""

    ladder = plan.ladder
    usage = plan.triage.usage
    return {
        "request": plan.request,
        "route": plan.route,
        "project": str(plan.root) if plan.root else None,
        "profile": plan.profile.model_dump(mode="json"),
        "signals": signal_lines(plan.triage.signals),
        "strategy": plan.strategy,
        "recipe": plan.recipe.name if plan.recipe else None,
        "stages": [
            {"name": s.name, "kind": s.kind, "why": s.why, "cost": s.cost} for s in plan.stages
        ],
        "ladder": {"start": ladder.start, "max": ladder.max_rung, "reasons": ladder.reasons}
        if ladder
        else None,
        "size": plan.size,
        "estimate_usd": {"first": plan.first_usd, "worst": plan.worst_usd},
        "budget_usd": plan.budget_usd,
        "triage_usage": None
        if usage is None
        else {
            "model": usage.model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cost_usd": usage.cost_usd,
        },
        "notes": plan.notes,
    }
