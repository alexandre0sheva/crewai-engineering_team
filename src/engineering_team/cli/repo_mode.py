"""What ``feature`` and ``fix`` share: a run on your project, never in your checkout blindly.

``run_repository_mode`` loads the request and has ``modes/isolation.py`` give the team a branch
(clean repository), a worktree (dirty one, or ``--worktree``) or a copy (a directory that is not
a repository, made a Git repository so the work can be diffed). It opens the run with the mode's
recipe, runs it, and shows how it ended. The run records ``CHANGE_SUMMARY.md`` and
``changes.patch`` in its run directory; nothing is ever pushed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import typer

from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.run_commands import present_run, wants_questions
from engineering_team.intake.bundle import RequestBundle
from engineering_team.modes.change_report import PATCH_FILE, SUMMARY_FILE
from engineering_team.modes.isolation import Isolation, isolate
from engineering_team.modes.run_options import write_run_options
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.runtime.context import new_run_id
from engineering_team.runtime.run_index import register_workspace
from engineering_team.settings import Settings, load_settings
from engineering_team.team import build_roster
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.workspaces import slugify_project_name

HEADING = re.compile(r"^\s*(#+|[-=*]{3,})")

# Builds the request: ``(settings) -> RequestBundle``; a ``ValueError`` is a usage error.
LoadRequest = Callable[[Settings], RequestBundle]


def slug_of(request: str, fallback: str = "change") -> str:
    """A short slug for the branch name: the first line of the request that says something."""

    for line in request.splitlines():
        if line.strip() and not HEADING.match(line):
            return line.strip()
    return fallback


def project_name_of(root: Path, given: str | None) -> str:
    try:
        return slugify_project_name(given or root.name)
    except ValueError:
        return "project"


@dataclass
class RepoRun:
    """How one repository-mode command differs from another."""

    mode: str  # the run's mode (``feature``, ``fix``, ``maintain``)
    root: Path
    load: LoadRequest
    options: SimpleNamespace  # the engine's CLI options (provider, profile, allow_web, ...)
    recipe: str = ""  # the recipe's name (default: the mode's own)
    worktree: bool = False
    allow_dirty: bool = False
    squash: bool = False
    interactive: bool | None = None
    stdin_used: bool = False
    project_name: str | None = None
    config: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    # What the command line asked beyond the request (``modes/run_options.py``).
    run_options: dict[str, Any] = field(default_factory=dict)
    # Checked after the recipe is loaded; raise ``ValueError`` for a usage error.
    check: Callable[[Settings], None] | None = None
    slug_fallback: str = "change"
    # Called once the run is open (its directory exists), before it starts.
    opened: Callable[[Any], None] | None = None
    # Called with the summary data after the run; may add to it (what ``--json`` prints).
    extra: Callable[[Any, dict[str, Any]], None] | None = None


def run_repository_mode(g: Globals, run: RepoRun) -> None:
    """Prepare, run, and report; exits the process with the run's exit code."""

    from engineering_team import main as engine

    if not run.root.is_dir():
        fail(f"{run.root} is not a directory.")
    ask = wants_questions(run.interactive, stdin_used=run.stdin_used)
    made: list[Isolation] = []
    printed: list[bool] = []

    def prepare() -> Any:
        label = project_name_of(run.root, run.project_name)
        overrides = {
            **engine._cli_overrides(run.options),
            "strategy": "pipeline",
            "project_name": label,
            **({"git.squash": True} if run.squash else {}),
            **run.overrides,
        }
        settings = load_settings(overrides=overrides, config_file=run.config)
        if run.check is not None:
            run.check(settings)
        bundle = run.load(settings)
        settings = settings.for_request(bundle.text)
        settings.check_ready(require_credentials=True)
        # A recipe that cannot run is refused before the team gets a branch, worktree, or copy.
        recipe = load_recipe(
            run.recipe or run.mode, run.root, teammates=build_roster(settings).members
        )
        run_id = new_run_id()
        isolation = isolate(
            run.root,
            run_id=run_id,
            slug=slug_of(bundle.text, run.slug_fallback),
            mode="worktree" if run.worktree else "auto",
            allow_dirty=run.allow_dirty,
            init_git=True,  # a copy becomes a repository: the work is diffed against the import
            workspace_root=settings.workspace_root,
            name=run.project_name,
        )
        made.append(isolation)
        register_workspace(settings.workspace_root, isolation.workspace, label)
        workspace = ProjectWorkspace.create(
            isolation.workspace,
            extra_commands=settings.command_allowlist,
            env_passthrough=settings.subprocess_env_allowlist,
        )
        prepared = engine._open_run(
            settings,
            mode=run.mode,
            requirements=bundle.text,
            context=bundle.context,
            workspace=workspace,
            run_id=run_id,
            recipe=recipe,
        )
        if run.run_options:
            write_run_options(prepared.ctx.run_dir, run.run_options)
        if run.opened is not None:
            try:
                run.opened(prepared)
            except BaseException:
                prepared.release(quiet=True)
                raise
        return prepared

    def work(prepared: Any) -> int:
        isolation = made[0]
        if not (g.json or g.quiet):
            console = g.console(stderr=True)
            console.print(f"Working on a {isolation.describe()}", markup=False, soft_wrap=True)
            for note in isolation.notes:
                console.print(f"  {note}", markup=False, soft_wrap=True)
        run_dir = prepared.ctx.run_dir

        def extra(data: dict[str, Any]) -> None:
            summary = run_dir / SUMMARY_FILE
            data["isolation"] = isolation.to_json()
            data["change_summary"] = str(summary) if summary.is_file() else None
            data["patch"] = str(run_dir / PATCH_FILE) if (run_dir / PATCH_FILE).is_file() else None
            if summary.is_file():
                data["report"] = str(summary)
            if run.extra is not None:
                run.extra(prepared, data)
            data["next_steps"] = [
                f"engineering-team diff {prepared.ctx.run_id}",
                f"engineering-team export-patch {prepared.ctx.run_id} --out change.patch",
                *data["next_steps"],
            ]

        code = present_run(prepared, g, ask=ask, extra=extra)
        printed.append(True)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)
