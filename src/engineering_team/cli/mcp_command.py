"""``mcp serve``: let other agents (Claude Code, Cursor, Codex, ...) hand work to the team."""

from __future__ import annotations

from typing import Annotated

import typer

from engineering_team.cli.context import fail
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.mcp_server import INSTALL_HINT, missing_dependencies

mcp_app = typer.Typer(
    help="The MCP server: other agents delegate work to the team (docs/USAGE.md).",
    no_args_is_help=True,
)


@mcp_app.command("serve")
def serve(
    ctx: typer.Context,
    repo: Annotated[
        list[str] | None,
        typer.Option(
            "--repo",
            help="A directory the server may act in (repeat for more; adds to mcp_serve.repos). "
            "A repository outside all of them is refused.",
        ),
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Serve the team as MCP tools over stdio (start_task, task_status, task_report, answer,
    cancel, list_runs). stdout carries the protocol; messages go to stderr."""

    import sys

    if missing := missing_dependencies():
        fail(f"The MCP server needs {', '.join(missing)}. Install it with: {INSTALL_HINT}")
    from engineering_team.mcp_server.guard import resolve_roots
    from engineering_team.mcp_server.handlers import TeamTools
    from engineering_team.mcp_server.launcher import McpLauncher
    from engineering_team.mcp_server.server import build_server

    settings = load(get_globals(ctx), config)
    try:
        roots = resolve_roots([*settings.mcp_serve.repos, *(repo or [])])
    except ValueError as exc:
        fail(str(exc))
    if not roots:
        sys.stderr.write(
            "Warning: no repository is allowed, so every start is refused. Pass --repo DIR "
            "(or set mcp_serve.repos).\n"
        )
    launcher = McpLauncher(
        settings.workspace_root,
        max_concurrent=settings.mcp_serve.max_concurrent_runs,
        config_file=config,
    )
    sys.stderr.write(
        f"engineering-team MCP server (stdio); acting in: "
        f"{', '.join(str(root) for root in roots) or 'nowhere'}\n"
    )
    build_server(TeamTools(settings, launcher, roots)).run(transport="stdio")
