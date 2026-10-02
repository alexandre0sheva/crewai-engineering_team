"""Project-scoped tools exposed to the engineering agents."""

from engineering_team.tools.registry import (
    CATALOGUE,
    GROUPS,
    PROJECT_GROUPS,
    ToolSpec,
    build_tools,
)
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.workspace import ProjectWorkspace, WorkspaceError

__all__ = [
    "CATALOGUE",
    "GROUPS",
    "PROJECT_GROUPS",
    "ProjectWorkspace",
    "ToolSpec",
    "WorkspaceError",
    "WriteScope",
    "build_tools",
]
