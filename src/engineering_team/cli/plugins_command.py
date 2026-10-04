"""``plugins list``: the plugin tools this setup would load, and the rows for ``docs/TOOLS.md``."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from engineering_team.cli.context import fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.extensions.plugin_loader import (
    PROJECT_PLUGIN_DIR,
    PluginError,
    PluginSet,
    load_plugins,
)

plugins_app = typer.Typer(
    help="Plugin tools: list what would be loaded (docs/CONFIGURATION.md).",
    no_args_is_help=False,
    invoke_without_command=True,
)
yes_no = {True: "yes", False: "no"}


def describe(plugins: PluginSet) -> dict[str, Any]:
    """The plugin set as plain data (the ``--json`` form)."""

    return {
        "tools": [
            {
                "name": item.tool.name,
                "group": item.tool.group,
                "read_only": item.tool.read_only,
                "needs_network": item.tool.needs_network,
                "source": item.source,
                "summary": item.summary,
            }
            for item in plugins.tools
        ],
        "not_loaded": list(plugins.skipped),
        "warnings": list(plugins.notes),
    }


def markdown_rows(plugins: PluginSet) -> list[str]:
    """The ``docs/TOOLS.md`` table rows for the plugin tools (all of them are command-gate free)."""

    return [
        f"| `{item.tool.name}` | `{item.tool.group}` | {yes_no[item.tool.read_only]} | "
        f"{yes_no[item.tool.needs_network]} | no | {item.summary} (plugin: {item.source}) |"
        for item in plugins.tools
    ]


@plugins_app.callback()
def plugins(ctx: typer.Context) -> None:
    """Plugin tools (`plugins list` is the default)."""

    if ctx.invoked_subcommand is None:
        plugins_list(ctx)


@plugins_app.command("list")
def plugins_list(
    ctx: typer.Context,
    config: ConfigOpt = None,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print the rows to paste into docs/TOOLS.md.")
    ] = False,
) -> None:
    """List the plugin tools: name, group, where each comes from; and what was not loaded."""

    g = get_globals(ctx)
    settings = load(g, config)
    try:
        found = load_plugins(settings)
    except PluginError as exc:
        fail(str(exc))
    if markdown:
        typer.echo("\n".join(markdown_rows(found)))
        return
    if g.json:
        print_json(describe(found))
        return
    console = g.console()
    if found.tools:
        table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        for name in ("Tool", "Group", "Read-only", "Network", "Source"):
            table.add_column(name, overflow="fold")
        for item in found.tools:
            table.add_row(
                item.tool.name,
                item.tool.group,
                yes_no[item.tool.read_only],
                yes_no[item.tool.needs_network],
                item.source,
            )
        console.print(table)
    else:
        console.print("No plugin tools are loaded.")
    for note in found.notes:
        console.print(f"Warning: {note}", style="yellow", markup=False, soft_wrap=True)
    for skipped in found.skipped:
        console.print(f"Not loaded: {skipped}", style="dim", markup=False, soft_wrap=True)
    console.print(
        f"Add a tool with @plugin_tool in {PROJECT_PLUGIN_DIR}/*.py (project plugins need "
        "allow_project_plugins = true) or an `engineering_team.tools` entry point; a teammate "
        "gets it by listing the tool's group under tool_groups (docs/CONFIGURATION.md).",
        style="dim",
        markup=False,
        soft_wrap=True,
    )
