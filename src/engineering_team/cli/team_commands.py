"""``team list`` and ``team show KEY``: who is on the team, and exactly how each is set up."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.settings import Settings, SettingsError
from engineering_team.team import Roster, TeamError, Teammate, build_roster, group_notes

team_app = typer.Typer(
    help="The teammates: list them and see how each is set up (docs/TEAM.md).",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _roster(g: Globals, config: str | None) -> tuple[Settings, Roster]:
    settings = load(g, config)
    try:
        return settings, build_roster(settings)
    except TeamError as exc:
        fail(str(exc))


def _model_text(settings: Settings, member: Teammate) -> str:
    try:
        resolved = settings.resolve_model(member.key, tier=member.tier, max_iter=member.max_iter)
    except SettingsError as exc:
        return f"not resolvable: {exc}"
    return f"{resolved.model} (tier {resolved.tier}, max_iter {resolved.max_iter})"


def describe(settings: Settings, member: Teammate) -> dict[str, Any]:
    """Everything about ``member`` as plain data (the ``--json`` form of ``team show``)."""

    def shown(text: str) -> str:
        return text.replace("{project_name}", settings.project_name)

    return {
        "key": member.key,
        "role": member.role_for(settings.project_name),
        "goal": shown(member.goal),
        "backstory": shown(member.backstory),
        "tier": member.tier,
        "model": _model_text(settings, member),
        "tool_groups": list(member.tool_groups),
        "allow_delegation": member.allow_delegation,
        "max_iter": member.max_iter,
        "enabled": member.enabled,
        "modes": list(member.modes),
        "stages": list(member.stages),
        "origin": member.origin,
        "notes": group_notes(member),
    }


@team_app.callback()
def team(ctx: typer.Context) -> None:
    """The teammates (`team list` is the default)."""

    if ctx.invoked_subcommand is None:
        team_list(ctx)


@team_app.command("list")
def team_list(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """List every teammate with its role, tier, tools, and where its definition came from."""

    g = get_globals(ctx)
    settings, roster = _roster(g, config)
    if g.json:
        print_json([describe(settings, m) for m in roster.all()])
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for name in ("Teammate", "Role", "Tier", "Tools", "State", "Source"):
        table.add_column(name, overflow="fold")
    for member in roster.all():
        table.add_row(
            member.key,
            describe(settings, member)["role"],
            member.tier,
            str(len(member.groups)),
            "enabled" if member.enabled else "disabled",
            member.origin,
        )
    console = g.console()
    console.print(table)
    console.print(
        "`team show KEY` shows one in full; change or add teammates under [team.KEY] in "
        "engineering-team.toml or in .engineering-team/team.yaml (docs/TEAM.md).",
        style="dim",
    )


@team_app.command("show")
def team_show(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="A teammate key from `team list`.")],
    config: ConfigOpt = None,
) -> None:
    """Show one teammate in full: prompt, tools, model, limits, and origin."""

    g = get_globals(ctx)
    settings, roster = _roster(g, config)
    try:
        member = roster.get(key)
    except TeamError as exc:
        fail(str(exc))
    data = describe(settings, member)
    if g.json:
        print_json(data)
        return
    console = g.console()
    rows = (
        ("Role", data["role"]),
        ("Goal", data["goal"]),
        ("Backstory", data["backstory"]),
        ("State", "enabled" if member.enabled else "disabled"),
        ("Model", data["model"]),
        ("Tool groups", ", ".join(data["tool_groups"]) or "none"),
        ("Delegation", "yes (hierarchical strategy only)" if member.allow_delegation else "no"),
        ("Modes", ", ".join(data["modes"]) or "all"),
        ("Extra stages", ", ".join(data["stages"]) or "none"),
        ("Source", member.origin),
    )
    console.print(member.key, style="bold", markup=False)
    for label, value in rows:
        console.print(f"  {label + ':':<14}{value}", markup=False, highlight=False, soft_wrap=True)
    for note in data["notes"]:
        console.print(f"  Note: {note}", style="yellow", markup=False, soft_wrap=True)
