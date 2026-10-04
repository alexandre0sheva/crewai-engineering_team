"""What the extension points add to a CrewAI ``Agent``: its MCP servers and knowledge sources."""

from __future__ import annotations

from typing import Any

from engineering_team.extensions.knowledge import knowledge_for
from engineering_team.extensions.mcp import mcps_for
from engineering_team.settings import Settings
from engineering_team.team import Teammate


def agent_extensions(settings: Settings, member: Teammate) -> dict[str, Any]:
    """Keyword arguments for ``Agent(...)``: ``mcps`` always, and ``knowledge_sources`` with
    ``embedder`` only when ``knowledge.sources`` is set and the teammate is meant to have them."""

    extras: dict[str, Any] = {"mcps": mcps_for(settings, member)}
    knowledge = knowledge_for(settings, member.key)
    if knowledge is not None:
        extras["knowledge_sources"], extras["embedder"] = knowledge
    return extras
