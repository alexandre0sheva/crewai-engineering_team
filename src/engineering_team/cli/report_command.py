"""``report``: write a run's report (HTML or Markdown) and optionally open it."""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer

from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, ProjectOpt, RunArg, find, load
from engineering_team.report import FORMATS, write_report


def report(
    ctx: typer.Context,
    run: RunArg = None,
    fmt: Annotated[
        str,
        typer.Option(
            "--format", callback=one_of(list(FORMATS)), help="html (self-contained) or md."
        ),
    ] = "html",
    open_it: Annotated[
        bool, typer.Option("--open", help="Open the report in your browser or default viewer.")
    ] = False,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Write the report of a run: summary, timeline, board, checks, cost, and changes."""

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    try:
        path = write_report(ref.run_dir, fmt)
    except OSError as exc:
        fail(f"Could not write the report: {exc}", 1)
    opened = False
    if open_it:
        opened = webbrowser.open(path.as_uri())
    if g.json:
        print_json({"run_id": ref.run_id, "format": fmt, "path": str(path), "opened": opened})
        return
    typer.echo(f"Wrote {path}")
    if open_it and not opened:
        typer.echo("Could not open it automatically; open the file above.", err=True)
