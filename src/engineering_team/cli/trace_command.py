"""``trace``: read what a run's agents did and saw, from its ``trajectory.jsonl``."""

from __future__ import annotations

from typing import Annotated

import typer

from engineering_team.cli.context import fail, one_of
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, ProjectOpt, RunArg, find, load
from engineering_team.runtime.run_store import TRAJECTORY_FILENAME
from engineering_team.runtime.trace import render_json, render_text, stages_of
from engineering_team.runtime.trajectory import read_trajectory

FORMATS = ("text", "json")


def trace(
    ctx: typer.Context,
    run: RunArg = None,
    stage: Annotated[
        str | None, typer.Option("--stage", help="Only this stage (as named in the headers).")
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", callback=one_of(FORMATS), help="text (default) or json."),
    ] = "text",
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Print a run's trajectory: each action, what it returned, and what the models said."""

    g = get_globals(ctx)
    ref = find(g, load(g, config), run, project_name)
    path = ref.run_dir / TRAJECTORY_FILENAME
    entries = list(read_trajectory(path)) if path.is_file() else None
    if entries is not None and stage is not None and stage not in stages_of(entries):
        known = ", ".join(stages_of(entries)) or "none"
        fail(f"Run {ref.run_id} has no stage {stage!r} in its trace (stages: {known}).")
    if g.json or fmt == "json":
        typer.echo(render_json(ref.run_id, entries, stage=stage))
    elif entries is None:
        typer.echo(
            f"Trajectory not recorded for run {ref.run_id} "
            "(runs made before 0.3.0 do not keep tool outputs)."
        )
    elif not entries:
        typer.echo(f"Run {ref.run_id} has not recorded anything yet.")
    else:
        typer.echo(render_text(entries, stage=stage), nl=False)
