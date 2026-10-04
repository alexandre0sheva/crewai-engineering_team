"""The ``engineering-team`` command: a Typer app, plus the bridge from the 0.1.0 invocation.

Global options (``--json``, ``--quiet``/``-q``, ``--verbose``/``-v``, ``--no-color``,
``--workspace-root DIR``) may be written anywhere on the command line; :func:`hoist_globals`
moves them in front of the command. Exit codes: 0 success or verified, 1 error, 2 usage or
configuration, 3 verification failed, 4 verification partial, 130 interrupted.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib import metadata
from typing import Annotated

import typer

from engineering_team.cli import (
    analyze_command,
    bench_command,
    feature_command,
    fix_command,
    init_command,
    maintain_command,
    recipes_command,
    report_command,
    review_command,
    run_commands,
    ui_command,
)
from engineering_team.cli import doctor as doctor_module
from engineering_team.cli import info_commands as info
from engineering_team.cli.context import Globals
from engineering_team.cli.plugins_command import plugins_app
from engineering_team.cli.team_commands import team_app
from engineering_team.runtime.context import reserve_run_id

COMMANDS = (
    "new", "resume", "status", "runs", "board", "cancel", "note", "pause", "unpause",
    "config", "doctor", "init", "examples", "team", "analyze", "feature", "fix", "maintain",
    "review", "recipes", "diff", "export-patch", "report", "ui", "bench", "plugins",
)  # fmt: skip
HELP_FLAGS = ("-h", "--help")
FLAGS = ("--json", "--quiet", "-q", "--verbose", "-v", "--no-color", "--answers-via-inbox")
VALUE_OPTIONS = ("--workspace-root", "--run-id")
BARE_DEPRECATION = (
    "Deprecated: bare `engineering-team` reading ./PROJECT_REQUEST.md is the 0.1.0 form. "
    "Use `engineering-team new`; the old form is removed in 0.3.0."
)
DEPRECATION = (
    "Deprecated: `engineering-team {old}` is the 0.1.0 form. Use `engineering-team new {old}`; "
    "the old form is removed in 0.3.0."
)

app = typer.Typer(
    name="engineering-team",
    help="An AI engineering team that builds software from a request.",
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": list(HELP_FLAGS)},
)
config_app = typer.Typer(help="Inspect the effective configuration.", no_args_is_help=True)
examples_app = typer.Typer(help="The bundled example requests.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(examples_app, name="examples")
app.add_typer(team_app, name="team")
app.add_typer(plugins_app, name="plugins")
app.add_typer(recipes_command.recipes_app, name="recipes")
app.add_typer(bench_command.bench_app, name="bench")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"engineering-team {metadata.version('engineering_team')}")
        raise typer.Exit()


@app.callback()
def global_options(
    ctx: typer.Context,
    json: Annotated[
        bool, typer.Option("--json", help="Machine-readable output on stdout.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Only warnings, errors, and the result.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show CrewAI's own console output.")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Plain text without colors.")
    ] = False,
    workspace_root: Annotated[
        str | None,
        typer.Option(help="Parent directory of generated projects (default: ./workspace)."),
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
    run_id: Annotated[
        str | None, typer.Option(hidden=True, help="Name the run (used by the web UI).")
    ] = None,
    answers_via_inbox: Annotated[
        bool,
        typer.Option(hidden=True, help="Take the team's questions' answers from the inbox."),
    ] = False,
) -> None:
    try:
        reserve_run_id(run_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    ctx.obj = Globals(
        json=json,
        quiet=quiet,
        verbose=verbose,
        no_color=no_color,
        workspace_root=workspace_root,
        run_id=run_id,
        answers_via_inbox=answers_via_inbox,
    )


app.command()(run_commands.new)
app.command()(run_commands.resume)
app.command()(analyze_command.analyze)
app.command()(feature_command.feature)
app.command()(fix_command.fix)
app.command()(maintain_command.maintain)
app.command()(review_command.review)
app.command()(feature_command.diff)
app.command("export-patch")(feature_command.export_patch)
app.command()(report_command.report)
app.command()(ui_command.ui)
app.command()(info.status)
app.command()(info.runs)
app.command()(info.board)
app.command()(info.cancel)
app.command()(info.note)
app.command()(info.pause)
app.command()(info.unpause)
app.command()(doctor_module.doctor)
app.command()(init_command.init)
config_app.command("show")(info.config_show)
examples_app.command("list")(info.examples_list)


@examples_app.command(
    "run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def examples_run(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="An example from `examples list`.")],
) -> None:
    """Build an example (same as `new --example NAME`; other `new` options are accepted)."""

    code = main(["new", "--example", name, *ctx.args, *_forward_globals(ctx)])
    raise typer.Exit(code)


def _forward_globals(ctx: typer.Context) -> list[str]:
    from engineering_team.cli.context import get

    g = get(ctx)
    out = [
        flag
        for flag, on in (
            ("--json", g.json),
            ("--quiet", g.quiet),
            ("--verbose", g.verbose),
            ("--no-color", g.no_color),
        )
        if on
    ]
    return [*out, *(["--workspace-root", g.workspace_root] if g.workspace_root else [])]


# -- argument handling ------------------------------------------------------------------------


def hoist_globals(argv: Sequence[str]) -> list[str]:
    """Move global options to the front, so ``runs --json`` works as well as ``--json runs``."""

    front: list[str] = []
    rest: list[str] = []
    items = list(argv)
    index = 0
    while index < len(items):
        item = items[index]
        if item == "--":
            rest.extend(items[index:])
            break
        if item in FLAGS:
            front.append(item)
        elif item in VALUE_OPTIONS and index + 1 < len(items):
            front.extend([item, items[index + 1]])
            index += 1
        elif item.split("=", 1)[0] in VALUE_OPTIONS and "=" in item:
            front.append(item)
        else:
            rest.append(item)
        index += 1
    return [*front, *rest]


def command_in(argv: Sequence[str]) -> str | None:
    """The subcommand named on the command line, or ``None`` for the 0.1.0 form (options only)."""

    index = 0
    while index < len(argv):
        item = argv[index]
        if item in FLAGS or item in HELP_FLAGS or item == "--version":
            index += 1
        elif item in VALUE_OPTIONS:
            index += 2
        elif item.split("=", 1)[0] in VALUE_OPTIONS and "=" in item:
            index += 1
        else:
            return item if item in COMMANDS else None
    return None


def _only_globals(arguments: Sequence[str]) -> bool:
    index = 0
    while index < len(arguments):
        if arguments[index] in FLAGS:
            index += 1
        elif arguments[index] in VALUE_OPTIONS:
            index += 2
        else:
            return False
    return True


def main(argv: Sequence[str]) -> int:
    """Run the command line ``argv`` (without the program name); returns the exit code."""

    arguments = hoist_globals(argv)
    wants_help = any(item in HELP_FLAGS or item == "--version" for item in arguments)
    if command_in(arguments) is None and not wants_help:
        if not _only_globals(arguments):
            # 0.1.0: `engineering-team --request-file FILE` means `new` with those options.
            old = " ".join(item for item in arguments if item not in FLAGS)
            sys.stderr.write(DEPRECATION.format(old=old) + "\n")
            arguments = _insert_command(arguments, "new")
        elif _has_default_request():
            sys.stderr.write(BARE_DEPRECATION + "\n")
            arguments = _insert_command(arguments, "new")
        else:
            arguments = [*arguments, "--help"]
    try:
        app(args=arguments, prog_name="engineering-team", standalone_mode=True)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 0


def _insert_command(arguments: list[str], command: str) -> list[str]:
    """Put ``command`` after any leading global options."""

    index = 0
    while index < len(arguments):
        if arguments[index] in FLAGS:
            index += 1
        elif arguments[index] in VALUE_OPTIONS:
            index += 2
        else:
            break
    return [*arguments[:index], command, *arguments[index:]]


def _has_default_request() -> bool:
    from pathlib import Path

    return (Path.cwd() / "PROJECT_REQUEST.md").is_file()
