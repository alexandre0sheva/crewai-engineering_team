"""Commands that look at runs and the setup: status, runs, board, cancel, note, pause, config."""

from __future__ import annotations

import time
from importlib import resources
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from engineering_team.board.models import BoardState
from engineering_team.cli.console import RunInfo, clock, header, render_run
from engineering_team.cli.context import Globals, fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.kanban import render_kanban
from engineering_team.cli.snapshot import RunWatcher, data_of
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.runtime.budget import Budget
from engineering_team.runtime.inbox import post_command
from engineering_team.runtime.locks import workspace_in_use
from engineering_team.runtime.run_index import RunRef, find_runs, locate_run
from engineering_team.settings import Settings, load_settings

RunArg = Annotated[
    str | None,
    typer.Argument(help="Run id, or enough of it to be unambiguous (default: the latest run)."),
]
ProjectOpt = Annotated[
    str | None, typer.Option(help="Only look in this project (default: every project).")
]
ConfigOpt = Annotated[str | None, typer.Option(help="Path to a config file.")]
STATUS_STYLE = {
    "succeeded": "green",
    "failed": "red",
    "cancelled": "yellow",
    "interrupted": "yellow",
    "running": "cyan",
    "pending": "dim",
}


def load(g: Globals, config: str | None = None, **overrides: Any) -> Settings:
    found = {k: v for k, v in {**g.overrides(), **overrides}.items() if k != "verbose"}
    try:
        return load_settings(overrides=found, config_file=config)
    except ValueError as exc:
        fail(str(exc))


def find(g: Globals, settings: Settings, run: str | None, project: str | None) -> RunRef:
    try:
        return locate_run(settings.workspace_root, run, project)
    except ValueError as exc:
        fail(str(exc))


def _info(settings: Settings, ref: RunRef) -> RunInfo:
    return RunInfo(
        ref.project,
        ref.manifest.strategy,
        Budget.from_settings(settings.budget),
        settings.price_table(),
    )


# -- status, runs, board -----------------------------------------------------------------------


def status(
    ctx: typer.Context,
    run: RunArg = None,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Where a run stands: stage by stage, progress, cost, and what is blocked."""

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    info = _info(settings, ref)
    state = RunWatcher(ref.run_dir, info.prices).poll()
    if g.json:
        data = data_of(state)
        data.update(
            project=ref.project,
            strategy=ref.manifest.strategy,
            mode=ref.manifest.mode,
            verdict=ref.manifest.verdict,
            workspace=str(ref.workspace),
        )
        print_json(data)
        return
    console = g.console()
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for name in ("Stage", "Status", "Attempts", "Note"):
        table.add_column(name)
    for stage in ref.manifest.stages:
        table.add_row(
            stage.name,
            Text(stage.status, style=STATUS_STYLE.get(stage.status, "")),
            str(stage.attempts),
            stage.detail,
        )
    parts: list[Any] = [header(state, info), table]
    if state.progress.blocked:
        parts.append(Text("Blocked", style="bold red"))
        parts.extend(
            Text(f"  {b.id} {b.title}: {b.reason}", style="red") for b in state.progress.blocked
        )
    verdict = f" · verdict {ref.manifest.verdict}" if ref.manifest.verdict else ""
    parts.append(Text(f"\nWorkspace {ref.workspace}{verdict}", style="dim"))
    console.print(Group(*parts))


def runs(
    ctx: typer.Context,
    project_name: ProjectOpt = None,
    limit: Annotated[int, typer.Option(min=1, help="How many of the newest runs to show.")] = 20,
    config: ConfigOpt = None,
) -> None:
    """List runs, newest last."""

    g = get_globals(ctx)
    settings = load(g, config)
    found = find_runs(settings.workspace_root, project_name)[-limit:]
    if g.json:
        print_json([_run_data(ref) for ref in found])
        return
    if not found:
        g.console().print("No runs yet. Start one with: engineering-team new --example tiny-notes")
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for name in ("Run", "Project", "Status", "Started", "Took", "Cost"):
        table.add_column(name, no_wrap=name == "Run")
    for ref in found:
        manifest, summary = ref.manifest, ref.manifest.summary
        shown = manifest.status + (f" ({manifest.verdict})" if manifest.verdict else "")
        if summary is None:
            cost = "-"
        elif summary.estimated_cost_usd is None:
            cost = "unknown"
        else:
            cost = f"${summary.estimated_cost_usd:.4f}"
        table.add_row(
            ref.run_id,
            ref.project,
            Text(shown, style=STATUS_STYLE.get(manifest.status, "")),
            manifest.created.astimezone().strftime("%m-%d %H:%M"),
            clock(summary.duration_seconds) if summary else "-",
            cost,
        )
    g.console().print(table)


def _run_data(ref: RunRef) -> dict[str, Any]:
    manifest, summary = ref.manifest, ref.manifest.summary
    return {
        "run_id": ref.run_id,
        "project": ref.project,
        "mode": manifest.mode,
        "strategy": manifest.strategy,
        "status": manifest.status,
        "verdict": manifest.verdict,
        "created": manifest.created.isoformat(),
        "duration_seconds": summary.duration_seconds if summary else None,
        "tokens": summary.usage.total_tokens if summary else None,
        "estimated_cost_usd": summary.estimated_cost_usd if summary else None,
    }


def board(
    ctx: typer.Context,
    run: RunArg = None,
    watch: Annotated[
        bool, typer.Option(help="Keep updating until the run ends (Ctrl-C to stop).")
    ] = False,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """The task board of a run, as a kanban."""

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    info = _info(settings, ref)
    watcher = RunWatcher(ref.run_dir, info.prices)
    console = g.console()
    if g.json:
        print_json(data_of(watcher.poll()))
        return
    if watch and g.interactive:
        try:
            with Live(console=console, refresh_per_second=4, transient=False) as live:
                while True:
                    state = watcher.poll()
                    live.update(render_run(state, info, width=console.width))
                    if not state.running:
                        break
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        return
    state = watcher.poll()
    console.print(header(state, info))
    console.print(render_kanban(state.cards, width=console.width, now=state.now))


# -- cancel, note, pause --------------------------------------------------------------------


def cancel(
    ctx: typer.Context,
    run: RunArg = None,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Ask a run in another process to stop at its next safe point."""

    from engineering_team.pipeline.runner import cancel_run

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    message = cancel_run(ref.workspace, ref.run_id)
    if g.json:
        print_json({"run_id": ref.run_id, "message": message})
    else:
        g.console().print(message, markup=False)


def note(
    ctx: typer.Context,
    run: Annotated[str, typer.Argument(help="Run id (or a unique start of it).")],
    text: Annotated[str, typer.Argument(help="What the team should know or do differently.")],
    card: Annotated[
        str | None, typer.Option(help="Send it to this card's assignee (e.g. K-004).")
    ] = None,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Steer a run: the team reads your note at its next step."""

    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    if card:
        try:
            board_state = BoardState.model_validate_json(
                (ref.run_dir / "board.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            fail(f"Run {ref.run_id} has no task board yet.")
        if card not in {c.id for c in board_state.cards}:
            known = ", ".join(c.id for c in board_state.cards if c.kind != "user_note")
            fail(f"No card {card!r} in run {ref.run_id}. Cards: {known or 'none'}.")
    try:
        post_command(ref.run_dir, "note", text=text, card=card)
    except ValueError as exc:
        fail(str(exc))
    active = workspace_in_use(ref.workspace)
    when = (
        "at its next step"
        if active
        else f"when you resume it (engineering-team resume {ref.run_id})"
    )
    message = f"Note queued for run {ref.run_id}; the team sees it {when}."
    if g.json:
        print_json({"run_id": ref.run_id, "queued": True, "active": active, "card": card})
    else:
        g.console().print(message, markup=False)


def _pause(
    ctx: typer.Context, run: str | None, project_name: str | None, config: str | None, kind: str
) -> None:
    g = get_globals(ctx)
    settings = load(g, config)
    ref = find(g, settings, run, project_name)
    if not workspace_in_use(ref.workspace):
        fail(
            f"Run {ref.run_id} is not running ({ref.manifest.status}); there is nothing to {kind}."
        )
    post_command(ref.run_dir, kind)
    message = (
        f"Run {ref.run_id} will pause at the next tool call of each agent."
        if kind == "pause"
        else f"Run {ref.run_id} will continue."
    )
    if g.json:
        print_json({"run_id": ref.run_id, "queued": kind})
    else:
        g.console().print(message, markup=False)


def pause(
    ctx: typer.Context,
    run: RunArg = None,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Pause a running run at its agents' next tool call."""

    _pause(ctx, run, project_name, config, "pause")


def unpause(
    ctx: typer.Context,
    run: RunArg = None,
    project_name: ProjectOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Let a paused run continue."""

    _pause(ctx, run, project_name, config, "unpause")


# -- config, examples, team ---------------------------------------------------------------------


def config_show(
    ctx: typer.Context,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    config: ConfigOpt = None,
) -> None:
    """Print every setting with where its value came from."""

    g = get_globals(ctx)
    overrides = {k: v for k, v in (("provider", provider), ("profile", profile)) if v}
    settings = load(g, config, **overrides)
    rows = settings.describe()
    if g.json:
        print_json([row.__dict__ for row in rows])
    else:
        width = max(len(row.key) for row in rows)
        value_width = min(max(len(row.value) for row in rows), 70)
        for row in rows:
            typer.echo(f"{row.key:<{width}}  {row.value:<{value_width}}  [{row.source}]")
    for note_text in [*settings.sdk_problems(), *settings.missing_credentials()]:
        typer.echo(f"Note: {note_text}.", err=True)


def examples_list(ctx: typer.Context) -> None:
    """The example requests bundled with the package."""

    from engineering_team.main import available_examples, load_example

    g = get_globals(ctx)
    items = []
    for name in available_examples():
        text = load_example(name)
        title = next(
            (
                line.lstrip("# ").strip()
                for line in text.splitlines()
                if line.strip() and not line.startswith("<!--")
            ),
            name,
        )
        items.append({"name": name, "title": title})
    if g.json:
        print_json(items)
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("Example")
    table.add_column("What it builds")
    for item in items:
        table.add_row(item["name"], item["title"])
    g.console().print(table)
    g.console().print("Run one with: engineering-team examples run NAME", style="dim")


def team(ctx: typer.Context) -> None:
    """The teammates (a configurable registry arrives in a later release)."""

    import yaml

    g = get_globals(ctx)
    text = (resources.files("engineering_team") / "config" / "agents.yaml").read_text(
        encoding="utf-8"
    )
    agents = yaml.safe_load(text) or {}
    members = [
        {
            "key": key,
            "role": str(spec.get("role", key)).strip(),
            "goal": " ".join(str(spec.get("goal", "")).split()),
        }
        for key, spec in agents.items()
    ]
    if g.json:
        print_json(members)
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for name in ("Teammate", "Role", "Goal"):
        table.add_column(name, overflow="fold")
    for member in members:
        table.add_row(member["key"], member["role"], member["goal"][:90])
    g.console().print(table)
    g.console().print(
        "Custom teammates and per-teammate tools are not configurable yet.", style="dim"
    )
