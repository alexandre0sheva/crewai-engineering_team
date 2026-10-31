"""``continue``: a follow-up to an earlier run, in the same thread.

``engineering-team continue <run> "<follow-up>"`` plans and runs the follow-up like ``do`` does,
but in the repository, branch (or worktree, or copy) of the run it follows, with that run's spec,
plan, answer, report summary and verdict as context and its profile as the prior of the triage.
None of the earlier run's evidence is reused: the follow-up is verified again on its own
revision. ``--implement`` carries out the plan a plan-only run stored.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import typer

from engineering_team.cli import do_command, do_plan
from engineering_team.cli.context import fail, one_of
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.run_commands import wants_questions
from engineering_team.intake.bundle import STDIN
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.threads import (
    ThreadLink,
    follow_up_request,
    implementation_mode,
    load_parent,
    prior_of,
    project_root,
    thread_context,
)
from engineering_team.pipeline.strategies import STRATEGY_NAMES
from engineering_team.runtime.run_index import locate_run
from engineering_team.settings import load_settings


def continue_run(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="The run to follow up (enough of its id).")],
    text: Annotated[
        str | None,
        typer.Argument(metavar="[FOLLOW_UP]", help="What to do next, in words ('-' reads stdin)."),
    ] = None,
    request: Annotated[
        str | None, typer.Option(help="The follow-up, inline (same as the argument).")
    ] = None,
    request_file: Annotated[
        list[str] | None,
        typer.Option(help="Markdown or text file with the follow-up; repeat to merge."),
    ] = None,
    implement: Annotated[
        bool,
        typer.Option(help="Implement the plan a plan-only run stored (the text adds to it)."),
    ] = False,
    context_dir: Annotated[
        str | None,
        typer.Option(help="Directory of reference documents the team may read (copied in)."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Start without asking after the preview.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option(help="Stop after the preview: change nothing, start nothing.")
    ] = False,
    allow_dirty: Annotated[
        bool,
        typer.Option(
            help="Work in your checkout even with uncommitted changes on the thread's branch "
            "(they become part of the follow-up's first commit)."
        ),
    ] = False,
    squash: Annotated[
        bool, typer.Option(help="Make the follow-up's commits one commit when it succeeds.")
    ] = False,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--non-interactive",
            help="Let the team ask you questions (needs a terminal; default: no).",
        ),
    ] = None,
    project_name: Annotated[
        str | None, typer.Option(help="Project of the run (default: found by id).")
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    strategy: Annotated[
        str | None,
        typer.Option(callback=one_of(list(STRATEGY_NAMES)), help="How the team is orchestrated."),
    ] = None,
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
    """Follow up an earlier run: same repository and branch, its work as context, checked again."""

    g = get_globals(ctx)
    if text is not None and request is not None:
        fail("Use only one of the FOLLOW_UP argument and --request.")
    inline = text if text is not None else request
    files = [*(request_file or [])]
    if inline == STDIN:
        inline, files = None, [STDIN, *files]
    options = SimpleNamespace(
        provider=provider, profile=profile, strategy=strategy, allow_web=allow_web, checks=checks,
        sandbox=sandbox, no_git=False, verbose=g.verbose, workspace_root=g.workspace_root,
        project_name=project_name,
    )  # fmt: skip
    ask = wants_questions(interactive, stdin_used=STDIN in files)

    def make_plan() -> do_plan.DoPlan:
        from engineering_team import main as engine

        settings = load_settings(overrides=engine._cli_overrides(options), config_file=config)
        parent = load_parent(locate_run(settings.workspace_root, run_id, project_name))
        mode = implementation_mode(parent) if implement else None
        words = (
            follow_up_request(inline or "", parent, implement=implement)
            if inline or implement
            else ""
        )
        bundle = engine.load_bundle(
            inline_request=words or None,
            request_files=files,
            example=None,
            context_dir=context_dir,
            settings=settings,
        )
        settings = settings.for_request(bundle.text)
        root = project_root(parent)
        if root is not None and not Path(root).is_dir():
            raise ValueError(
                f"The workspace of run {parent.ref.run_id} ({root}) is gone. "
                "Start a new run with `do`."
            )
        plan = do_command._plan(
            settings, bundle, root, mode, None, ask, dry_run, explicit=False, prior=prior_of(parent)
        )
        plan.context = thread_context(parent, implement=implement)
        plan.thread = ThreadLink(parent, implement)
        plan.notes.append(
            f"A follow-up of run {parent.ref.run_id} (thread {parent.ref.thread_id})."
        )
        return plan

    do_command.plan_and_run(
        ctx, g, make_plan,
        options=options, config=config, ask=ask, context_dir=context_dir, yes=yes,
        dry_run=dry_run, interactive=interactive, stdin_used=STDIN in files,
        allow_dirty=allow_dirty, squash=squash,
    )  # fmt: skip
