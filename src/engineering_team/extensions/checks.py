"""Checks of the extension settings that need the file system, run before a run starts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.extensions.conventions import check_conventions_file
from engineering_team.extensions.knowledge import collect_documents

if TYPE_CHECKING:
    from engineering_team.settings import Settings


def check_extensions(settings: Settings, base: Path | None = None) -> None:
    """Raise a ``ValueError`` (a usage error) naming the fix when ``conventions_file`` or a
    ``knowledge.sources`` entry cannot be used. Plugins are checked by loading them
    (``extensions.plugin_loader.load_plugins``), hooks and MCP servers by their schema."""

    root = base or Path.cwd()
    check_conventions_file(settings.conventions_file, root)
    if settings.knowledge.sources:
        collect_documents(settings.knowledge, root)
