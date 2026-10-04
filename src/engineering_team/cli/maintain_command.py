"""``maintain``: a maintenance task on an existing project, never in your checkout blindly.

The presets are recipes (``modes/recipes/``): ``add-tests``, ``refactor``, ``upgrade-deps``,
``docs``, ``security-audit`` and ``custom`` (the goal you write). ``--task`` also takes the name
of a recipe you wrote yourself (``.engineering-team/recipes/`` or
``~/.config/engineering-team/recipes/``). Where the team works, and what it leaves behind, is
the same as for ``feature`` (``cli/repo_mode.py``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, one_of
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.repo_mode import RepoRun, run_repository_mode
from engineering_team.intake.bundle import RequestBundle
from engineering_team.intake.errors import IntakeError
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.pipeline.recipe_list import list_recipes
from engineering_team.settings import Settings

PRESETS = ("add-tests", "refactor", "upgrade-deps", "docs", "security-audit", "custom")
MAX_GOAL_BYTES = 200_000


def tasks_for(root: Path) -> list[str]:
    """The tasks ``--task`` accepts here: the presets, and recipes of your own."""

    own = [i.name for i in list_recipes(root) if i.source != "bundled" and i.name not in PRESETS]
    return [*PRESETS, *own]


def _goal(goal: str | None, goal_file: str | None) -> str | None:
    if goal is not None and goal_file is not None:
        fail("Use only one of --goal and --goal-file.")
    if goal_file is None:
        return goal
    path = Path(goal_file).expanduser()
    try:
        data = path.read_bytes()[: MAX_GOAL_BYTES + 1]
    except OSError as exc:
        fail(f"Cannot read the goal file {path}: {exc}")
    if len(data) > MAX_GOAL_BYTES:
        fail(f"{path} is larger than {MAX_GOAL_BYTES:,} bytes.")
    return data.decode("utf-8", errors="replace")


def _request(task: str, goal: str | None) -> str:
    head = f"Maintenance task: {task}"
    return f"{head}\n\n{goal.strip()}" if goal and goal.strip() else head


def maintain(
    ctx: typer.Context,
    task: Annotated[
        str | None,
        typer.Option(
            help="add-tests, refactor, upgrade-deps, docs, security-audit, custom, or a recipe "
            "of your own (see `recipes list`)."
        ),
    ] = None,
    repo: Annotated[str, typer.Option(help="The project directory (default: here).")] = ".",
    goal: Annotated[
        str | None,
        typer.Option(help="What to do or focus on, in words (required for --task custom)."),
    ] = None,
    goal_file: Annotated[
        str | None, typer.Option(help="A file with the goal (instead of --goal).")
    ] = None,
    fix: Annotated[
        bool,
        typer.Option(help="security-audit only: also fix the serious findings (default: report)."),
    ] = False,
    context_dir: Annotated[
        str | None,
        typer.Option(help="Directory of reference documents the team may read (copied in)."),
    ] = None,
    worktree: Annotated[
        bool,
        typer.Option(help="Work in a separate worktree (the default when the tree is dirty)."),
    ] = False,
    allow_dirty: Annotated[
        bool,
        typer.Option(
            help="Work in your checkout, on a new branch, even with uncommitted changes "
            "(they become part of the team's first commit)."
        ),
    ] = False,
    squash: Annotated[
        bool, typer.Option(help="Make the team's commits one commit when the run succeeds.")
    ] = False,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--non-interactive",
            help="Let the team ask you questions (needs a terminal; default: no).",
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(help="Name of the run's project, and of the copy for a non-Git directory."),
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    allow_web: Annotated[bool, typer.Option(help="Let the team use the web tools.")] = False,
    checks: Annotated[
        str | None, typer.Option(help="YAML file of your own checks (kept outside the project).")
    ] = None,
    sandbox: Annotated[
        str | None,
        typer.Option(callback=one_of(["local", "docker"]), help="Where commands run."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Maintain an existing project: tests, refactors, upgrades, docs, audits, or your own goal."""

    g = get_globals(ctx)
    root = Path(repo).expanduser().resolve()
    known = tasks_for(root) if root.is_dir() else list(PRESETS)
    if task is None:
        fail(f"Pass --task: one of {', '.join(known)}.")
    if task not in known:
        fail(f"Unknown task {task!r}. Choose one of: {', '.join(known)}.")
    text = _goal(goal, goal_file)
    if task == "custom" and not (text and text.strip()):
        fail("--task custom needs the goal: pass --goal or --goal-file.")
    if fix and task != "security-audit":
        fail("--fix applies to --task security-audit only.")
    options = SimpleNamespace(
        provider=provider, profile=profile, allow_web=allow_web, checks=checks, sandbox=sandbox,
        verbose=g.verbose, workspace_root=g.workspace_root,
    )  # fmt: skip

    def check(settings: Settings) -> None:
        if task == "upgrade-deps" and not settings.tools.dev.allow_network:
            raise ValueError(
                "upgrade-deps installs packages, which needs the network: set "
                "tools.dev.allow_network = true (it is the default) and run it again."
            )

    def load(settings: Settings) -> RequestBundle:
        try:
            return RequestBundle.from_sources(
                text=_request(task, text), context_dir=context_dir, limits=settings.intake
            )
        except IntakeError as exc:
            raise ValueError(str(exc)) from exc

    run_options: dict[str, Any] = {"task": task, "fix_findings": fix}
    run_repository_mode(
        g,
        RepoRun(
            mode="maintain",
            recipe=task,
            root=root,
            load=load,
            options=options,
            worktree=worktree,
            allow_dirty=allow_dirty,
            squash=squash,
            interactive=interactive,
            stdin_used=False,
            project_name=project_name,
            config=config,
            slug_fallback=task,
            run_options=run_options,
            check=check,
        ),
    )
