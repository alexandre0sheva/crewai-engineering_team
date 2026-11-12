"""The MCP server (``engineering-team mcp serve``): other agents hand work to the team.

Claude Code, Cursor, Codex and any other MCP client can start a run, watch it, read its report,
ask a question about a repository, and cancel it, without leaving their tool. The server is a
thin layer, like the web UI's: it never runs a team in its own process. A run is a CLI subprocess
(``launcher.py``, the same one the UI starts), and everything it reports is read from the run
directory the CLI already writes. ``guard.py`` is the safety boundary: which repositories the
server may act in and how much one run may spend.

The MCP SDK is the optional ``mcp`` extra (``uv sync --extra mcp``).
"""

from __future__ import annotations

import importlib.util

# (importable module, package that provides it)
REQUIRED_MODULES = (("mcp", "mcp"),)
INSTALL_HINT = "uv sync --extra mcp  (or: pip install 'engineering_team[mcp]')"
SERVER_NAME = "engineering-team"


def missing_dependencies() -> list[str]:
    """The packages of the ``mcp`` extra that are not installed (empty when it is complete)."""

    return [
        package for module, package in REQUIRED_MODULES if importlib.util.find_spec(module) is None
    ]
