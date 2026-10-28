"""What a tool is in the catalogue: a name, a group, flags, and the factory that builds it."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from crewai.tools import BaseTool

from engineering_team.tools.support import ToolEnv

# ``factory`` builds every tool of one module for a run, keyed by tool name; ``build_tools``
# calls each distinct factory once per run and picks the tools it needs.
ToolFactory = Callable[[ToolEnv], Mapping[str, BaseTool]]

# The groups that work on the project's files and commands; the rest coordinate the team.
PROJECT_GROUPS = ("fs_read", "search", "fs_write", "command")
GROUPS = (
    *PROJECT_GROUPS,
    "dev",
    "code_intel",
    "git_read",
    "knowledge",
    "web",
    "browser",
    "runtime",
    "board",
    "notes",
    "human",
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    read_only: bool
    needs_network: bool
    needs_command_gate: bool
    summary: str
    factory: ToolFactory


def _spec(
    name: str,
    group: str,
    factory: ToolFactory,
    summary: str,
    *,
    read_only: bool,
    gate: bool = False,
) -> ToolSpec:
    return ToolSpec(name, group, read_only, False, gate, summary, factory)
