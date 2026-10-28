"""``profile``: where a run's time and money went, session by session."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from engineering_team.cli.context import get as get_globals
from engineering_team.cli.context import one_of
from engineering_team.cli.info_commands import ConfigOpt, ProjectOpt, RunArg, find, load
from engineering_team.runtime.profile import build_profile
from engineering_team.runtime.profile_text import profile_data, render_profile

FORMATS = ("text", "json")


def profile(
    ctx: typer.Context,
    run: RunArg = None,
    fmt: Annotated[
        str, typer.Option("--format", callback=one_of(FORMATS), help="text (default) or json.")
    ] = "text",
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Profile a run: sessions by cost and time, prompt parts, critical path, re-reads, failures."""

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    result = build_profile(ref.run_dir, settings.price_table())
    if g.json or fmt == "json":
        typer.echo(json.dumps(profile_data(result), indent=2, default=str, ensure_ascii=False))
    else:
        typer.echo(render_profile(result), nl=False)
