"""``new`` and ``resume``: start or continue a run and show it live."""

from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli import summary
from engineering_team.cli.ask import TerminalAnswerer
from engineering_team.cli.console import LiveDisplay, PlainDisplay, RunInfo
from engineering_team.cli.context import Globals, fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.intake.bundle import STDIN
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.pipeline.runner import execute_run
from engineering_team.pipeline.strategies import STRATEGY_NAMES
from engineering_team.runtime.budget import Budget


def present_run(prepared: Any, g: Globals, *, ask: bool = False) -> int:
    """Run ``prepared`` with the right display, then show (or print as JSON) how it ended.

    With ``ask`` the team's questions are put to the person at the terminal.
    """

    from engineering_team import main as engine

    ctx = prepared.ctx
    console = g.console()
    info = RunInfo(
        ctx.settings.project_name,
        prepared.strategy.name,
        Budget.from_settings(ctx.settings.budget),
        ctx.prices,
    )
    display: Any = None
    if g.interactive:
        display = LiveDisplay(console, info, ctx.run_dir)
    elif not g.json:
        display = PlainDisplay(console, ctx.run_dir, ctx.prices, quiet=g.quiet)
    # Anything CrewAI or a tool prints must not corrupt the JSON document on stdout.
    stdout = contextlib.redirect_stdout(sys.stderr) if g.json else contextlib.nullcontext()
    answering: Any = contextlib.nullcontext()
    if ask:
        pause = display.paused if isinstance(display, LiveDisplay) else None
        answering = TerminalAnswerer(ctx.human, g.console(stderr=True), pause=pause)
    with stdout, display() if display else contextlib.nullcontext(), answering:
        result = execute_run(
            ctx, prepared.bundle, strategy=prepared.strategy, recipe=prepared.recipe
        )
    code = engine.exit_code_for(result)
    data = summary.build(ctx, result, code, resumable=prepared.strategy.resumable)
    if g.json:
        print_json(data)
    elif g.interactive:
        summary.render(data, console)
    else:
        summary.render_plain(data, console)
    line = engine.failure_line(result, resumable=prepared.strategy.resumable)
    if line is not None:  # the panel is for people; this line is for logs and scripts
        print(line, file=sys.stderr)
    return code


def _finish(g: Globals, code: int, printed: list[bool]) -> None:
    """Exit with ``code``; a ``--json`` run that failed before it could report says so in JSON."""

    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)


def new(
    ctx: typer.Context,
    request: Annotated[
        str | None, typer.Option(help="Inline product requirements ('-' reads stdin).")
    ] = None,
    request_file: Annotated[
        list[str] | None,
        typer.Option(
            help="Markdown or text file with the requirements; repeat to merge several "
            "('-' reads stdin)."
        ),
    ] = None,
    context_dir: Annotated[
        str | None,
        typer.Option(help="Directory of reference documents the team may read (copied in)."),
    ] = None,
    example: Annotated[
        str | None, typer.Option(help="A bundled example request (see `examples`).")
    ] = None,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--non-interactive",
            help="Let the team ask you questions (needs a terminal; default: no).",
        ),
    ] = None,
    project_name: Annotated[
        str | None, typer.Option(help="Workspace directory name (default: mvp-app).")
    ] = None,
    reset: Annotated[
        bool,
        typer.Option(help="Delete this project's workspace first (only if this tool made it)."),
    ] = False,
    force_reset: Annotated[
        bool, typer.Option(help="Like --reset, also for a directory this tool did not create.")
    ] = False,
    adopt: Annotated[bool, typer.Option(hidden=True)] = False,
    prepare_only: Annotated[
        bool,
        typer.Option(help="Validate inputs and prepare the workspace without calling a model."),
    ] = False,
    profile: Annotated[
        str | None,
        typer.Option(callback=one_of(list(PROFILE_NAMES)), help="Quality/cost routing."),
    ] = None,
    provider: Annotated[
        str | None, typer.Option(callback=one_of(list(PROVIDERS)), help="Model provider preset.")
    ] = None,
    strategy: Annotated[
        str | None,
        typer.Option(callback=one_of(list(STRATEGY_NAMES)), help="How the team is orchestrated."),
    ] = None,
    allow_web: Annotated[bool, typer.Option(help="Let the team use the web tools.")] = False,
    checks: Annotated[
        str | None, typer.Option(help="YAML file of your own checks (pipeline strategy).")
    ] = None,
    no_git: Annotated[bool, typer.Option(help="No Git repository or stage commits.")] = False,
    sandbox: Annotated[
        str | None,
        typer.Option(callback=one_of(["local", "docker"]), help="Where commands run."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Build a new project from a request (text, files, stdin, or a bundled example)."""

    from engineering_team import main as engine

    g = get_globals(ctx)
    if example is not None and (request is not None or request_file):
        fail("Use only one of --example, or --request/--request-file (those can be combined).")
    files = [*(request_file or [])]
    if request == STDIN:  # `--request -` is the request on stdin, ahead of any files
        request, files = None, [STDIN, *files]
    ask = wants_questions(interactive, stdin_used=STDIN in files)
    args = SimpleNamespace(
        request=request,
        request_file=files,
        context_dir=context_dir,
        example=example,
        project_name=project_name,
        workspace_root=g.workspace_root,
        reset=reset,
        force_reset=force_reset,
        adopt=adopt,
        prepare_only=prepare_only,
        profile=profile,
        provider=provider,
        strategy=strategy,
        allow_web=allow_web,
        checks=checks,
        no_git=no_git,
        sandbox=sandbox,
        config=config,
        verbose=g.verbose,
    )
    printed: list[bool] = []

    def work(prepared: Any) -> int | None:
        if prepare_only:
            with prepared.recorder.running():
                print(
                    f"Prepared project workspace: {prepared.ctx.workspace.root}", file=_text_out(g)
                )
            if g.json:
                print_json({"status": "prepared", "workspace": str(prepared.ctx.workspace.root)})
                printed.append(True)
            return None
        code = present_run(prepared, g, ask=ask)
        printed.append(True)
        return code

    code = engine._execute(
        lambda: engine._prepare_from_args(args),
        work,
        lambda p: p.release(quiet=bool(printed) or g.json),
    )
    _finish(g, code, printed)


def wants_questions(flag: bool | None, *, stdin_used: bool) -> bool:
    """Whether the team may ask the person questions: only on request (``--interactive``) and
    only when a terminal can answer (stdin is a TTY and is not carrying the request)."""

    if not flag:
        return False
    if stdin_used or not sys.stdin.isatty():
        print(
            "Not asking questions: stdin is not a terminal (or carries the request); "
            "running non-interactively.",
            file=sys.stderr,
        )
        return False
    return True


def _text_out(g: Globals) -> Any:
    return sys.stderr if g.json else sys.stdout


def resume(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="The run id, or enough of it to be unambiguous.")],
    request: Annotated[
        str | None, typer.Option(help="Inline requirements; a changed one starts a new run.")
    ] = None,
    request_file: Annotated[
        str | None, typer.Option(help="Requirements file; a changed one starts a new run.")
    ] = None,
    project_name: Annotated[
        str | None, typer.Option(help="Project of the run (default: found by id).")
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    allow_web: Annotated[bool, typer.Option(help="Enable the web tools.")] = False,
    sandbox: Annotated[
        str | None, typer.Option(callback=one_of(["local", "docker"]), help="Where commands run.")
    ] = None,
    checks: Annotated[
        str | None, typer.Option(help="Your checks file again (the one the run started with).")
    ] = None,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--non-interactive",
            help="Let the team ask you questions (needs a terminal; default: no).",
        ),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Continue a cancelled, interrupted, or failed run without redoing finished stages."""

    from engineering_team import main as engine
    from engineering_team.runtime.run_index import locate_run
    from engineering_team.settings import load_settings

    g = get_globals(ctx)
    if request is not None and request_file is not None:
        fail("Use only one of --request and --request-file.")
    args = SimpleNamespace(
        provider=provider, profile=profile, allow_web=allow_web, sandbox=sandbox, checks=checks,
        workspace_root=g.workspace_root, project_name=project_name, verbose=g.verbose,
    )  # fmt: skip
    printed: list[bool] = []

    def prepare() -> Any:
        settings = load_settings(overrides=engine._cli_overrides(args), config_file=config)
        ref = locate_run(settings.workspace_root, run_id, project_name)
        settings = settings.with_overrides(
            {"project_name": ref.project}, source=f"run {ref.run_id}"
        )
        text = (
            engine.load_requirements(
                inline_request=request, request_file=request_file, settings=settings
            )
            if request is not None or request_file is not None
            else None
        )
        return engine._prepare_resume(settings, ref.run_id, text)

    ask = wants_questions(interactive, stdin_used=False)

    def work(prepared: Any) -> int:
        code = present_run(prepared, g, ask=ask)
        printed.append(True)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    _finish(g, code, printed)
