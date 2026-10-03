"""``feature``, ``diff`` and ``export-patch``: add a feature to an existing project, then look at
and take away what the team did.

``feature`` never works in your checkout blindly: ``modes/isolation.py`` gives the team a new
branch (clean repository), a worktree (dirty one, or ``--worktree``), or a copy (a directory that
is not a repository, which is made a Git repository so the work can be diffed). The run records
a ``CHANGE_SUMMARY.md`` and a ``changes.patch`` in its run directory; nothing is ever pushed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.repo_mode import RepoRun, run_repository_mode
from engineering_team.intake.bundle import STDIN
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.isolation import read_isolation
from engineering_team.modes.repo_analyzer import standalone_git
from engineering_team.settings import Settings


def feature(
    ctx: typer.Context,
    repo: Annotated[str, typer.Option(help="The project directory (default: here).")] = ".",
    request: Annotated[
        str | None, typer.Option(help="The feature to add, inline ('-' reads stdin).")
    ] = None,
    request_file: Annotated[
        list[str] | None,
        typer.Option(help="Markdown or text file describing the feature; repeat to merge."),
    ] = None,
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
    """Add a feature to an existing project, on a branch, worktree, or copy (never blindly)."""

    from engineering_team import main as engine

    g = get_globals(ctx)
    files = [*(request_file or [])]
    if request == STDIN:
        request, files = None, [STDIN, *files]
    options = SimpleNamespace(
        provider=provider, profile=profile, allow_web=allow_web, checks=checks, sandbox=sandbox,
        verbose=g.verbose, workspace_root=g.workspace_root,
    )  # fmt: skip

    def load(settings: Settings) -> Any:
        return engine.load_bundle(
            inline_request=request, request_files=files, context_dir=context_dir, settings=settings
        )

    run_repository_mode(
        g,
        RepoRun(
            mode="feature",
            root=Path(repo).expanduser().resolve(),
            load=load,
            options=options,
            worktree=worktree,
            allow_dirty=allow_dirty,
            squash=squash,
            interactive=interactive,
            stdin_used=STDIN in files,
            project_name=project_name,
            config=config,
            slug_fallback="feature",
        ),
    )


# -- looking at the result ---------------------------------------------------------------------


def _located(ctx: typer.Context, run: str | None, project_name: str | None, config: str | None):  # noqa: ANN202
    from engineering_team.cli.info_commands import find, load

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    isolation = read_isolation(ref.workspace)
    if isolation is None or not isolation.base_commit:
        fail(
            f"Run {ref.run_id} has no recorded starting commit: only runs of `feature` (and the "
            "other repository modes) have a diff to show."
        )
    return g, ref, isolation


def diff(
    ctx: typer.Context,
    run: Annotated[str | None, typer.Argument(help="Run id (default: the latest run).")] = None,
    stat: Annotated[bool, typer.Option(help="Only the per-file summary.")] = False,
    project_name: Annotated[str | None, typer.Option(help="Only look in this project.")] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """What the team changed: the work against the commit it started from."""

    from engineering_team.git.port import GitError

    g, ref, isolation = _located(ctx, run, project_name, config)
    try:
        with standalone_git(ref.workspace) as port:
            text = (
                port.diff_stat(isolation.base_commit) if stat else port.diff(isolation.base_commit)
            )
    except GitError as exc:
        fail(str(exc), 1)
    if g.json:
        print_json(
            {"run_id": ref.run_id, "base": isolation.base_commit, "stat": stat, "diff": text}
        )
    else:
        typer.echo(text or "No changes.", nl=not text.endswith("\n"))


def export_patch(
    ctx: typer.Context,
    run: Annotated[str | None, typer.Argument(help="Run id (default: the latest run).")] = None,
    out: Annotated[str, typer.Option(help="Where to write the patch.")] = "change.patch",
    force: Annotated[bool, typer.Option(help="Overwrite the file if it exists.")] = False,
    project_name: Annotated[str | None, typer.Option(help="Only look in this project.")] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Write the team's change as a patch that `git apply` takes on the commit it started from."""

    from engineering_team.git.port import GitError

    g, ref, isolation = _located(ctx, run, project_name, config)
    target = Path(out).expanduser().resolve()
    if target.exists() and not force:
        fail(f"{target} already exists; pass --force to overwrite it.")
    if target.is_relative_to(ref.workspace):
        fail("Write the patch outside the project: it would be part of the change it describes.")
    try:
        with standalone_git(ref.workspace) as port:
            port.export_patch(target, isolation.base_commit)
    except GitError as exc:
        fail(str(exc), 1)
    if g.json:
        print_json({"run_id": ref.run_id, "base": isolation.base_commit, "patch": str(target)})
    else:
        base = (isolation.base_commit or "")[:12]
        typer.echo(f"Wrote {target}. Apply it on {base} with: git apply {target.name}")
