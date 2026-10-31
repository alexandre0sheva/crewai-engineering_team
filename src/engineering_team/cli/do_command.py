"""``do``: one entry point for every kind of request.

``engineering-team do "<request>"`` (or just ``engineering-team "<request>"``) triages the
request, composes the plan, shows both, asks to proceed (unless ``--yes`` or nothing can answer)
and then runs it with the same machinery as the older commands: a change to an existing project
goes through ``cli/repo_mode.py`` (a branch, worktree or copy, never your checkout blindly), a
new project through the ``new`` path, a review or a question about a project through ``review``
and ``analyze``. ``new``, ``feature``, ``fix``, ``maintain``, ``review`` and ``analyze`` are the
presets that force the intent and skip the preview; ``cli/do_plan.py`` is the planning half.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli import do_plan, do_run
from engineering_team.cli.context import Globals, fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.do_plan import with_notes  # noqa: F401  (re-exported)
from engineering_team.cli.do_run import PROFILE_FILE  # noqa: F401  (re-exported)
from engineering_team.cli.run_commands import wants_questions
from engineering_team.contracts import TaskProfile
from engineering_team.intake.bundle import STDIN, RequestBundle
from engineering_team.intake.clarify import settle
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.outcome import EXIT_INTERRUPTED, EXIT_PARTIAL
from engineering_team.pipeline.strategies import STRATEGY_NAMES
from engineering_team.runtime.interaction import HumanChannel
from engineering_team.settings import Settings, load_settings


class TerminalChannel(HumanChannel):
    """Asks the triage questions at the terminal, one prompt each (an empty answer skips)."""

    def ask(self, text: str, **_: Any) -> str | None:
        try:
            answer = typer.prompt(f"? {text}", default="", show_default=False, err=True)
        except (EOFError, typer.Abort):
            return None
        return answer.strip() or None


def detect_root(repo: str | None) -> Path | None:
    """The directory the request is about: ``--repo``, else the current directory when it is a
    Git repository, else ``None`` (a new project in the workspace root)."""

    if repo is not None:
        root = Path(repo).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"{root} is not a directory.")
        return root
    from engineering_team.git.port import GitError
    from engineering_team.modes.repo_analyzer import standalone_git

    here = Path.cwd()
    try:
        with standalone_git(here) as git:
            return here if git.is_repo() else None
    except (GitError, OSError):
        return None


def do(
    ctx: typer.Context,
    text: Annotated[
        str | None,
        typer.Argument(metavar="[REQUEST]", help="What you want, in words ('-' reads stdin)."),
    ] = None,
    request: Annotated[
        str | None, typer.Option(help="The request, inline (same as the argument).")
    ] = None,
    request_file: Annotated[
        list[str] | None,
        typer.Option(
            help="Markdown or text file with the request; repeat to merge ('-' is stdin)."
        ),
    ] = None,
    context_dir: Annotated[
        str | None,
        typer.Option(help="Directory of reference documents the team may read (copied in)."),
    ] = None,
    example: Annotated[
        str | None, typer.Option(help="A bundled example request (see `examples`).")
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option(
            help="The project directory (default: here when it is a Git repository, otherwise "
            "a new project is made in the workspace root)."
        ),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option(
            callback=one_of(do_plan.MODES),
            help=f"Force the kind of work instead of triaging it: {', '.join(do_plan.MODES)}.",
        ),
    ] = None,
    recipe: Annotated[
        str | None,
        typer.Option(help="Run this recipe (yours or bundled) instead of composing one."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Start without asking after the preview.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option(help="Stop after the preview: change nothing, start nothing.")
    ] = False,
    fix: Annotated[
        bool, typer.Option(help="For a security audit: also fix the serious findings.")
    ] = False,
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
            help="Let the team ask you questions (needs a terminal; default: no). "
            "--non-interactive also starts without asking after the preview.",
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(help="Name of the project (a new project's directory; default: mvp-app)."),
    ] = None,
    reset: Annotated[
        bool,
        typer.Option(help="New project: delete its workspace first (only if this tool made it)."),
    ] = False,
    force_reset: Annotated[
        bool, typer.Option(help="Like --reset, also for a directory this tool did not create.")
    ] = False,
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
    no_git: Annotated[bool, typer.Option(help="New project: no Git repository.")] = False,
    sandbox: Annotated[
        str | None,
        typer.Option(callback=one_of(["local", "docker"]), help="Where commands run."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Do what the request asks: triage it, show the plan, then build, change, fix or review."""

    g = get_globals(ctx)
    if text is not None and request is not None:
        fail("Use only one of the REQUEST argument and --request.")
    inline = text if text is not None else request
    files = [*(request_file or [])]
    if inline == STDIN:
        inline, files = None, [STDIN, *files]
    options = SimpleNamespace(
        provider=provider, profile=profile, strategy=strategy, allow_web=allow_web, checks=checks,
        sandbox=sandbox, no_git=no_git, verbose=g.verbose, workspace_root=g.workspace_root,
        project_name=project_name,
    )  # fmt: skip
    try:
        settings, bundle, root = _intake(options, config, inline, files, context_dir, example, repo)
        ask = wants_questions(interactive, stdin_used=STDIN in files)
        plan = _plan(settings, bundle, root, mode, recipe, ask, dry_run, explicit=repo is not None)
    except NeedsInfo as exc:
        sys.stderr.write(f"Error: {exc}\n")
        for number, question in enumerate(exc.questions, 1):
            sys.stderr.write(f"Question {number}: {question}\n")
        if g.json:
            print_json(
                {"status": "needs-info", "exit_code": EXIT_PARTIAL, "questions": exc.questions}
            )
        raise typer.Exit(EXIT_PARTIAL) from None
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except ValueError as exc:
        fail(str(exc))
    _show(g, plan)
    if dry_run:
        if g.json:
            print_json({"status": "planned", "exit_code": 0, "plan": do_plan.plan_document(plan)})
        raise typer.Exit(0)
    if not _proceed(g, yes=yes, interactive=interactive, stdin_used=STDIN in files):
        sys.stderr.write("Not started: nothing was changed.\n")
        raise typer.Exit(1)
    do_run.execute(
        ctx,
        g,
        plan,
        options=options,
        config=config,
        ask=ask,
        context_dir=context_dir,
        worktree=worktree,
        allow_dirty=allow_dirty,
        squash=squash,
        reset=reset or force_reset,
        force_reset=force_reset,
        fix=fix,
        stdin_used=STDIN in files,
    )


def _intake(
    options: SimpleNamespace,
    config: str | None,
    inline: str | None,
    files: list[str],
    context_dir: str | None,
    example: str | None,
    repo: str | None,
) -> tuple[Settings, RequestBundle, Path | None]:
    from engineering_team import main as engine

    settings = load_settings(overrides=engine._cli_overrides(options), config_file=config)
    bundle = engine.load_bundle(
        inline_request=inline,
        request_files=files,
        example=example,
        context_dir=context_dir,
        settings=settings,
    )
    return settings.for_request(bundle.text), bundle, detect_root(repo)


def _plan(
    settings: Settings,
    bundle: RequestBundle,
    root: Path | None,
    mode: str | None,
    recipe: str | None,
    ask: bool,
    dry_run: bool,
    *,
    explicit: bool,
) -> do_plan.DoPlan:
    """Triage and plan. A preview needs no credentials (the signals decide without them); a run
    that cannot start is refused before the triage call spends anything."""

    if not dry_run:
        settings.check_ready(require_credentials=True)

    def answers(result: Any) -> TaskProfile:
        channel = TerminalChannel(interactive=True) if ask else HumanChannel()
        return settle(result.profile, channel)

    plan = do_plan.plan_request(
        bundle.text, root, settings, mode=mode, recipe_name=recipe, answers=answers
    )
    if explicit and plan.root is None:
        raise ValueError(
            f"{root} has no project files. Run `do` without --repo to start a new project in "
            "the workspace root."
        )
    return plan


def _show(g: Globals, plan: do_plan.DoPlan) -> None:
    if g.quiet:
        return
    console = g.console()
    for line in do_plan.render_plan(plan):
        console.print(line, markup=False, highlight=False, soft_wrap=True)


def _proceed(g: Globals, *, yes: bool, interactive: bool | None, stdin_used: bool) -> bool:
    """Whether to start: asked only when a person can answer (a terminal, no ``--yes``, no
    ``--non-interactive``, nothing machine-readable on stdout)."""

    if yes or interactive is False or stdin_used or g.json or g.quiet:
        return True
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return True
    try:
        return typer.confirm("Start this?", default=True, err=True)
    except typer.Abort:
        return False
