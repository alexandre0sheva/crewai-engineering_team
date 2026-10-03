"""``analyze``: look at an existing project without changing it.

Without ``--deep`` the command is the deterministic profile only: languages, projects, detected
commands, entry points, tests, CI, conventions, and the Git state. It reads files and asks Git
read-only questions; nothing is written and no model is called. With ``--deep`` the codebase
analysts also read the code and write ``.engineering-team/codebase-map.md`` (reused while the
tree is unchanged); that is the only thing written besides the run's own record, and both live
in the controller's state directory, which Git is told to ignore (``.git/info/exclude``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.adopt import analysis_recipe
from engineering_team.modes.codebase_map import cached_map, map_path
from engineering_team.modes.profile_render import render_profile
from engineering_team.modes.repo_analyzer import analyze_repo, standalone_git
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.pipeline.state import RunBundle
from engineering_team.pipeline.strategies import get_strategy
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.session import RunRecorder
from engineering_team.runtime.snapshot import workspace_revision
from engineering_team.settings import Settings, load_settings
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.workspaces import slugify_project_name

REQUEST = "Analyse the existing codebase and write its codebase map."


def analyze(
    ctx: typer.Context,
    repo: Annotated[str, typer.Option(help="The project directory (default: here).")] = ".",
    deep: Annotated[
        bool,
        typer.Option(
            help="Also have the codebase analysts read the code and write "
            ".engineering-team/codebase-map.md (uses the model; the project is not changed)."
        ),
    ] = False,
    refresh: Annotated[
        bool, typer.Option(help="With --deep: write the map again even if the tree is unchanged.")
    ] = False,
    provider: Annotated[
        str | None, typer.Option(callback=one_of(list(PROVIDERS)), help="Model provider preset.")
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(callback=one_of(list(PROFILE_NAMES)), help="Quality/cost routing."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Profile an existing project (languages, commands, tests, CI, Git); --deep also maps it."""

    from engineering_team import main as engine

    g = get_globals(ctx)
    root = Path(repo).expanduser().resolve()
    if not root.is_dir():
        fail(f"{root} is not a directory.")
    if refresh and not deep:
        fail("--refresh needs --deep: the profile alone is always recomputed.")
    found = analyze_repo(root)
    if not deep:
        _show(g, found, deep=False)
        raise typer.Exit(0)

    from engineering_team.cli.run_commands import present_run

    args = SimpleNamespace(provider=provider, profile=profile, verbose=g.verbose)
    printed: list[bool] = []

    def prepare() -> Any:
        overrides = {
            **engine._cli_overrides(args),
            "strategy": "pipeline",
            "git.enabled": False,  # no stage commits: analysis never makes history in your repo
            "project_name": _project_name(root),
        }
        settings = load_settings(overrides=overrides, config_file=config)
        settings.check_ready(require_credentials=True)
        if refresh:
            map_path(root).unlink(missing_ok=True)
        return open_analysis(settings, root)

    def work(prepared: Any) -> int:
        if not (g.json or g.quiet):
            g.console().print(render_profile(found), markup=False, highlight=False, soft_wrap=True)
        code = present_run(prepared, g, extra=lambda: _document(root, found, deep=True))
        printed.append(True)
        if not g.json:
            _show_map(g, root)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)


def open_analysis(settings: Settings, root: Path) -> Any:
    """Open a run for the read-only analysis of ``root`` (the project is not an owned workspace:
    no ownership marker is written). The caller releases the lock when the run is over."""

    from engineering_team.main import PreparedRun

    workspace = ProjectWorkspace.create(
        root,
        extra_commands=settings.command_allowlist,
        env_passthrough=settings.subprocess_env_allowlist,
    )
    with standalone_git(root) as git:
        if git.is_repo():  # before anything is written there
            git.exclude_controller_state()
    run_id = new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    try:
        context = RunContext.create(settings, workspace, run_id=run_id)
        recipe = analysis_recipe()
        recorder = RunRecorder.begin(
            context, mode="analyze", request=REQUEST, strategy="pipeline", recipe=recipe.name
        )
    except BaseException:
        lock.release()
        raise
    return PreparedRun(
        ctx=context,
        inputs={},
        lock=lock,
        recorder=recorder,
        bundle=RunBundle(requirements=REQUEST),
        strategy=get_strategy("pipeline"),
        recipe=recipe,
    )


def _project_name(root: Path) -> str:
    try:
        return slugify_project_name(root.name)
    except ValueError:
        return "project"


# -- output -----------------------------------------------------------------------------------


def _map_info(root: Path) -> dict[str, Any]:
    path = map_path(root)
    if not path.is_file():
        return {"path": None, "current": None}
    tree = workspace_revision(ProjectWorkspace.create(root))
    return {"path": str(path), "current": cached_map(root, tree) is not None}


def _document(root: Path, found: RepoProfile, *, deep: bool) -> dict[str, Any]:
    return {"profile": found.model_dump(mode="json"), "map": _map_info(root), "deep": deep}


def _show(g: Any, found: RepoProfile, *, deep: bool) -> None:
    root = Path(found.root)
    if g.json:
        print_json(_document(root, found, deep=deep))
        return
    console = g.console()
    console.print(render_profile(found), markup=False, highlight=False, soft_wrap=True)
    _show_map(g, root, hint=not deep)


def _show_map(g: Any, root: Path, *, hint: bool = False) -> None:
    info = _map_info(root)
    console = g.console()
    if info["path"] is None:
        if hint:
            console.print(
                "Codebase map: none yet. Run with --deep to have the analysts write one.",
                markup=False,
                soft_wrap=True,
            )
        return
    state = "describes the current tree" if info["current"] else "out of date: run with --deep"
    console.print(
        f"Codebase map: {info['path']} ({state})", markup=False, highlight=False, soft_wrap=True
    )
