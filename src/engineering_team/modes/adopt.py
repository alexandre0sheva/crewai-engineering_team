"""Adopting an existing project: the controller actions and recipes behind ``adopt.yaml``.

The ``adopt`` recipe (``modes/recipes/adopt.yaml``) has three stages: ``profile`` and ``baseline``
are controller actions registered here, ``map`` is an ``analyze`` stage run by ``map_stage``.
``analysis_recipe`` is the subset ``engineering-team analyze --deep`` runs (no baseline: it would
run the project's commands, and ``analyze`` only reads). Where the team works is decided before a
run starts, by ``modes/isolation.isolate``.
"""

from __future__ import annotations

from pathlib import Path

from engineering_team.atomic_io import atomic_write_json
from engineering_team.modes.baseline import run_baseline
from engineering_team.modes.isolation import read_isolation
from engineering_team.modes.repo_analyzer import analyze_repo
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.recipes import Recipe, load_recipe
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

PROFILE_FILE = Path(CONTROLLER_DIRECTORY) / "repo-profile.json"
ANALYSIS_STAGES = ("profile", "map")


def save_profile(root: Path, profile: RepoProfile) -> Path:
    path = root / PROFILE_FILE
    atomic_write_json(path, profile.model_dump(mode="json"))
    return path


def adopt_recipe() -> Recipe:
    return load_recipe("adopt")


def analysis_recipe() -> Recipe:
    """The ``adopt`` stages that only read: the profile and the codebase map."""

    full = adopt_recipe()
    return Recipe(
        name="analyze",
        description="Profile an existing project and map its code (read-only).",
        stages=[stage for stage in full.stages if stage.name in ANALYSIS_STAGES],
    )


@register_action("repo_profile")
def profile_action(ctx: RunContext, state: PipelineState) -> str:
    """Profile the workspace (deterministic, no model) and keep it in the state and on disk."""

    profile = analyze_repo(ctx.workspace.root, ctx.git)
    state.profile = profile
    isolation = read_isolation(ctx.workspace.root)  # how the workspace was made, if it was
    state.isolation = isolation.to_json() if isolation is not None else None
    save_profile(ctx.workspace.root, profile)
    commands = len(profile.commands)
    return (
        f"{profile.files} files, {profile.primary_language}, {len(profile.stacks)} project(s), "
        f"{commands} command(s) detected."
    )


@register_action("baseline")
def baseline_action(ctx: RunContext, state: PipelineState) -> str:
    """Run the detected checks before any change and record which already fail."""

    profile = state.profile or analyze_repo(ctx.workspace.root, ctx.git)
    report = run_baseline(ctx, profile)
    state.baseline = report
    failing = sum(1 for check in report.checks if check.status == "failed")
    return (
        f"{len(report.checks)} check(s) ran; {failing} already fail "
        f"({len(report.known_failures)} known failure(s))."
    )
