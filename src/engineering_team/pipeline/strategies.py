"""Strategies: how a run is orchestrated. ``Strategy.run(ctx, recipe, bundle) -> RunResult``.

* ``PipelineStrategy``: the recipe's stages as a Flow with typed hand-offs, a task board,
  resume, and cancellation.
* ``SingleAgentStrategy``: one agent with every tool and one task: the baseline the other
  strategies are measured against. It runs through the same pipeline machinery as a one-stage
  recipe, so it has a board card, a manifest stage, resume, and cancellation too.
* ``TeamStrategy``: a lead agent does the work and calls teammates on demand
  (``pipeline/lead.py``); the recipe's controller skeleton and verification stay. Also through
  the pipeline machinery, as the recipe :func:`~engineering_team.pipeline.lead.lead_recipe`
  derives.

A strategy runs *inside* ``recorder.running()`` (``pipeline/runner.py``) and returns how its work
ended; an exception means the run failed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from engineering_team.contracts import RunVerdict
from engineering_team.pipeline.flow import run_flow
from engineering_team.pipeline.lead import lead_recipe
from engineering_team.pipeline.recipes import Recipe, StageSpec, load_recipe
from engineering_team.pipeline.stages import CrewStageRunner, StageRunner
from engineering_team.pipeline.state import PipelineState, RunBundle, RunResult
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.run_store import RunStore

STRATEGY_NAMES = ("pipeline", "single", "team")
# Strategies that run a recipe of the mode (the others bring their own).
RECIPE_STRATEGIES = ("pipeline", "team")
# Strategies 0.2.0 could record in a manifest that no longer exist: their runs stay viewable, but
# cannot be resumed.
RETIRED_STRATEGIES = ("hierarchical",)
DEFAULT_RECIPE = "new"

# The one-stage recipe of the single-agent baseline.
SINGLE_RECIPE = Recipe(
    name="single",
    description="One agent with every tool builds the whole project in one task.",
    stages=[
        StageSpec(
            name="build",
            kind="agent",
            teammates=["generalist_engineer"],
            inputs=["request"],
            outputs=["file:README.md", "file:docs/release-report.md"],
            verification_policy="artifacts",
        )
    ],
)

RunnerFactory = Callable[[], StageRunner]


class Strategy(Protocol):
    name: str
    resumable: bool

    def recipe_for(self, recipe: Recipe | None) -> Recipe: ...

    def run(self, ctx: RunContext, recipe: Recipe | None, bundle: RunBundle) -> RunResult: ...


def _result(ctx: RunContext, status: str, error: str = "", *, verdict: bool = False) -> RunResult:
    """The result of a strategy; ``verdict`` reads how verification ended from the pipeline
    state (only a run that verified has one, and only an end that is final is reported)."""

    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    reached: RunVerdict | None = None
    if verdict and (state := PipelineState.load(ctx.run_dir)) is not None:
        reached = state.verification.verdict
        if reached == "verified" and status != "succeeded":
            reached = None  # verified once, but the run did not finish: nothing to report
        if state.needs_info and status == "failed":
            reached = "needs-info"  # a fix that could not reproduce the bug stopped to ask
    return RunResult(
        run_id=ctx.run_id,
        status=status,  # type: ignore[arg-type]
        error=error,
        stages=manifest.stages,
        workspace=ctx.workspace.root,
        verdict=reached,
    )


class PipelineStrategy:
    name = "pipeline"
    resumable = True

    def __init__(self, runner_factory: RunnerFactory = CrewStageRunner) -> None:
        self._runner_factory = runner_factory

    def recipe_for(self, recipe: Recipe | None) -> Recipe:
        return recipe or load_recipe(DEFAULT_RECIPE)

    def run(self, ctx: RunContext, recipe: Recipe | None, bundle: RunBundle) -> RunResult:
        status, error = run_flow(ctx, self.recipe_for(recipe), bundle, self._runner_factory())
        return _result(ctx, status, error, verdict=True)


class SingleAgentStrategy(PipelineStrategy):
    name = "single"

    def recipe_for(self, recipe: Recipe | None) -> Recipe:
        return SINGLE_RECIPE


class TeamStrategy(PipelineStrategy):
    name = "team"

    def recipe_for(self, recipe: Recipe | None) -> Recipe:
        return lead_recipe(recipe or load_recipe(DEFAULT_RECIPE))


def get_strategy(name: str, runner_factory: RunnerFactory | None = None) -> Strategy:
    """The strategy called ``name``; ``runner_factory`` swaps the stage runner (tests)."""

    if name in RETIRED_STRATEGIES:
        raise ValueError(
            f"The '{name}' strategy was removed in 0.3.0. Choose from: {', '.join(STRATEGY_NAMES)}."
        )
    factory = runner_factory or CrewStageRunner
    if name == "pipeline":
        return PipelineStrategy(factory)
    if name == "single":
        return SingleAgentStrategy(factory)
    if name == "team":
        return TeamStrategy(factory)
    raise ValueError(f"Unknown strategy '{name}'. Choose from: {', '.join(STRATEGY_NAMES)}.")


def recipe_name_for(strategy: str) -> str | None:
    """The recipe recorded in the manifest for a strategy (``None``: it does not use one)."""

    return {"pipeline": DEFAULT_RECIPE, "single": SINGLE_RECIPE.name, "team": DEFAULT_RECIPE}.get(
        strategy
    )
