"""Strategies: how a run is orchestrated. ``Strategy.run(ctx, recipe, bundle) -> RunResult``.

* ``HierarchicalStrategy``: the 0.1.0 crew, a manager delegating to four specialists over six
  fixed tasks (``config/tasks.yaml``). Not resumable.
* ``PipelineStrategy``: the recipe's stages as a Flow with typed hand-offs, a task board,
  resume, and cancellation.
* ``SingleAgentStrategy``: one agent with every tool and one task: the baseline the other
  strategies are measured against. It runs through the same pipeline machinery as a one-stage
  recipe, so it has a board card, a manifest stage, resume, and cancellation too.

A strategy runs *inside* ``recorder.running()`` (``pipeline/runner.py``) and returns how its work
ended; an exception means the run failed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from engineering_team.crew import EngineeringTeam
from engineering_team.pipeline.flow import run_flow
from engineering_team.pipeline.recipes import Recipe, StageSpec, load_recipe
from engineering_team.pipeline.stages import CrewStageRunner, StageRunner
from engineering_team.pipeline.state import RunBundle, RunResult
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.run_store import RunStore

STRATEGY_NAMES = ("hierarchical", "pipeline", "single")
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

    def recipe_for(self, recipe: Recipe | None) -> Recipe | None: ...

    def run(self, ctx: RunContext, recipe: Recipe | None, bundle: RunBundle) -> RunResult: ...


def _result(ctx: RunContext, status: str, error: str = "") -> RunResult:
    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    return RunResult(
        run_id=ctx.run_id,
        status=status,  # type: ignore[arg-type]
        error=error,
        stages=manifest.stages,
        workspace=ctx.workspace.root,
    )


class HierarchicalStrategy:
    name = "hierarchical"
    resumable = False

    def recipe_for(self, recipe: Recipe | None) -> Recipe | None:
        return None  # six fixed tasks in config/tasks.yaml, not a recipe

    def run(self, ctx: RunContext, recipe: Recipe | None, bundle: RunBundle) -> RunResult:
        EngineeringTeam(ctx).crew().kickoff(inputs=bundle.inputs)
        return _result(ctx, "succeeded")


class PipelineStrategy:
    name = "pipeline"
    resumable = True

    def __init__(self, runner_factory: RunnerFactory = CrewStageRunner) -> None:
        self._runner_factory = runner_factory

    def recipe_for(self, recipe: Recipe | None) -> Recipe:
        return recipe or load_recipe(DEFAULT_RECIPE)

    def run(self, ctx: RunContext, recipe: Recipe | None, bundle: RunBundle) -> RunResult:
        status, error = run_flow(ctx, self.recipe_for(recipe), bundle, self._runner_factory())
        return _result(ctx, status, error)


class SingleAgentStrategy(PipelineStrategy):
    name = "single"

    def recipe_for(self, recipe: Recipe | None) -> Recipe:
        return SINGLE_RECIPE


def get_strategy(name: str, runner_factory: RunnerFactory | None = None) -> Strategy:
    """The strategy called ``name``; ``runner_factory`` swaps the stage runner (tests)."""

    if name == "hierarchical":
        return HierarchicalStrategy()
    factory = runner_factory or CrewStageRunner
    if name == "pipeline":
        return PipelineStrategy(factory)
    if name == "single":
        return SingleAgentStrategy(factory)
    raise ValueError(f"Unknown strategy '{name}'. Choose from: {', '.join(STRATEGY_NAMES)}.")


def recipe_name_for(strategy: str) -> str | None:
    """The recipe recorded in the manifest for a strategy (``None``: it does not use one)."""

    return {"pipeline": DEFAULT_RECIPE, "single": SINGLE_RECIPE.name}.get(strategy)
