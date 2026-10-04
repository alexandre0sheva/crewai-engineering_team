"""MCP servers per teammate.

``[mcp.<name>]`` in the configuration defines a server; a teammate gets it by listing
``mcp:<name>`` in its tool groups or by being named in the server's ``roles``. The 0.1.0
``docs_mcp_urls`` setting (``ENGINEERING_DOCS_MCP_URLS``) stays as an alias: its servers go to
every teammate with ``mcp:docs``. The ``smoke`` profile attaches none, as before.

An MCP server is code and data this project does not control: a ``command`` server is a program
started on this machine with your privileges (outside the Docker sandbox, which only covers
the execution backend), and any server's answers are untrusted. :func:`trust_notes` says so.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from crewai.mcp import MCPServerHTTP, MCPServerSSE, MCPServerStdio
from crewai.mcp.filters import create_static_tool_filter

from engineering_team.extensions.config import McpServer
from engineering_team.settings import Settings
from engineering_team.team import Teammate

McpReference = str | MCPServerHTTP | MCPServerSSE | MCPServerStdio


def group_name(name: str) -> str:
    return f"mcp:{name}"


def build_server(server: McpServer) -> MCPServerHTTP | MCPServerSSE | MCPServerStdio:
    """The CrewAI configuration of one server (nothing connects until an agent runs)."""

    allowed = (
        create_static_tool_filter(allowed_tool_names=list(server.allow_tools))
        if server.allow_tools
        else None
    )
    if server.command is not None:
        return MCPServerStdio(
            command=server.command,
            args=list(server.args),
            env=dict(server.env) or None,
            tool_filter=allowed,
        )
    assert server.url is not None  # McpServer guarantees a url or a command
    if server.transport == "sse":
        return MCPServerSSE(url=server.url, tool_filter=allowed)
    return MCPServerHTTP(url=server.url, tool_filter=allowed)


def servers_for(settings: Settings, member: Teammate) -> list[str]:
    """Names of the ``[mcp.*]`` servers ``member`` gets, in configuration order."""

    return [
        name
        for name, server in settings.mcp.items()
        if group_name(name) in member.tool_groups or member.key in server.roles
    ]


def mcps_for(settings: Settings, member: Teammate) -> list[McpReference] | None:
    """What to pass as ``Agent(mcps=...)`` for ``member``: ``None`` for no MCP at all."""

    if not settings.docs_mcp_enabled:  # the smoke profile: a cheap, closed run
        return None
    references: list[McpReference] = []
    if member.uses_docs_mcp:
        references.extend(settings.docs_mcp_urls)
    references.extend(build_server(settings.mcp[name]) for name in servers_for(settings, member))
    return references or None


def _where(server: McpServer) -> str:
    if server.command is not None:
        return f"starts `{server.command}` on this machine with your privileges"
    assert server.url is not None
    return f"connects to {urlsplit(server.url).hostname}"


def trust_notes(settings: Settings, members: list[Teammate]) -> list[str]:
    """One warning per MCP server that is attached to someone (and one for the legacy URLs)."""

    notes: list[str] = []
    if not settings.docs_mcp_enabled:
        return notes
    for name, server in settings.mcp.items():
        who = [m.key for m in members if m.enabled and name in servers_for(settings, m)]
        if who:
            notes.append(
                f"MCP server '{name}' {_where(server)} for {', '.join(who)}. Only use servers "
                "you trust: its answers are untrusted data, and its tools run outside the "
                "execution sandbox."
            )
    if settings.docs_mcp_urls and any(m.uses_docs_mcp and m.enabled for m in members):
        notes.append(
            f"{len(settings.docs_mcp_urls)} documentation MCP server(s) from docs_mcp_urls are "
            "attached to teammates with mcp:docs. Only use servers you trust."
        )
    return notes
