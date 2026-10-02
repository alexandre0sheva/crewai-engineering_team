"""The tool catalogue: every agent tool, its group, and how a run's tools are assembled.

``docs/TOOLS.md`` lists the same tools; a test keeps the two in step. New tool tasks add a
``ToolSpec`` here and a row there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from engineering_team.tools.command_tools import make_command_tools
from engineering_team.tools.read_tools import make_read_tools
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.search_tools import make_search_tools
from engineering_team.tools.support import ToolEnv, never_cache
from engineering_team.tools.write_tools import make_write_tools

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

# ``factory`` builds every tool of one module for a run, keyed by tool name; ``build_tools``
# calls each distinct factory once per run and picks the tools it needs.
ToolFactory = Callable[[ToolEnv], Mapping[str, BaseTool]]

GROUPS = ("fs_read", "search", "fs_write", "command")


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


CATALOGUE: tuple[ToolSpec, ...] = (
    _spec(
        "List Project Files",
        "fs_read",
        make_read_tools,
        "Flat listing below a path.",
        read_only=True,
    ),
    _spec(
        "Read Project File",
        "fs_read",
        make_read_tools,
        "Read one whole text file (up to 250 KB).",
        read_only=True,
    ),
    _spec(
        "Read File Range",
        "fs_read",
        make_read_tools,
        "Numbered lines from a start line; also reads command logs.",
        read_only=True,
    ),
    _spec(
        "Read Many Files",
        "fs_read",
        make_read_tools,
        "Several files or ranges in one call.",
        read_only=True,
    ),
    _spec(
        "File Info",
        "fs_read",
        make_read_tools,
        "Type, size, lines, mtime, binary check.",
        read_only=True,
    ),
    _spec(
        "Project Tree",
        "fs_read",
        make_read_tools,
        "Directory tree with file counts and sizes.",
        read_only=True,
    ),
    _spec(
        "Search Project Files",
        "search",
        make_search_tools,
        "Literal or regex content search with globs and context.",
        read_only=True,
    ),
    _spec(
        "Find Files",
        "search",
        make_search_tools,
        "Glob file search sorted by name or recency.",
        read_only=True,
    ),
    _spec(
        "Project Outline",
        "search",
        make_search_tools,
        "Top-level symbols per source file.",
        read_only=True,
    ),
    _spec(
        "Repo Map",
        "search",
        make_search_tools,
        "Token-budgeted map of the most-referenced files and symbols.",
        read_only=True,
    ),
    _spec(
        "Write Project File",
        "fs_write",
        make_write_tools,
        "Create or overwrite a text file.",
        read_only=False,
    ),
    _spec(
        "Replace In Project File",
        "fs_write",
        make_write_tools,
        "Exact-text replacement with an occurrence check.",
        read_only=False,
    ),
    _spec(
        "Delete Project Path",
        "fs_write",
        make_write_tools,
        "Delete a file or directory.",
        read_only=False,
    ),
    _spec(
        "Apply Patch",
        "fs_write",
        make_write_tools,
        "Atomic multi-file unified diff or edit list.",
        read_only=False,
    ),
    _spec("Move Path", "fs_write", make_write_tools, "Move or rename a path.", read_only=False),
    _spec("Copy Path", "fs_write", make_write_tools, "Copy a file or directory.", read_only=False),
    _spec(
        "Make Directory",
        "fs_write",
        make_write_tools,
        "Create a directory with parents.",
        read_only=False,
    ),
    _spec(
        "Workspace Changes",
        "fs_write",
        make_write_tools,
        "Added/modified/deleted files since the run started, with a diff.",
        read_only=True,
    ),
    _spec(
        "Run Project Command",
        "command",
        make_command_tools,
        "Run an allowlisted command (no shell) with a streamed log.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "List Scripts",
        "command",
        make_command_tools,
        "package.json, Makefile, justfile, and pyproject scripts.",
        read_only=True,
    ),
    _spec(
        "Run Script",
        "command",
        make_command_tools,
        "Run a listed project script by name.",
        read_only=False,
        gate=True,
    ),
)


def build_tools(
    ctx: RunContext,
    *,
    groups: Iterable[str] | None = None,
    write_scope: WriteScope | None = None,
    read_only: bool = False,
) -> list[BaseTool]:
    """Build one run's tools, in catalogue order.

    ``groups`` selects tool groups (default: all). ``write_scope`` limits which paths the
    write tools may change; reads stay unrestricted. ``read_only`` leaves out every tool
    that changes files or runs commands.
    """

    wanted = tuple(GROUPS if groups is None else groups)
    unknown = sorted(set(wanted) - set(GROUPS))
    if unknown:
        raise ValueError(
            f"Unknown tool group(s): {', '.join(unknown)}. Known: {', '.join(GROUPS)}."
        )
    env = ToolEnv(ctx, write_scope)
    bundles: dict[ToolFactory, Mapping[str, BaseTool]] = {}
    tools: list[BaseTool] = []
    for spec in CATALOGUE:
        if spec.group not in wanted or (read_only and not spec.read_only):
            continue
        if spec.factory not in bundles:
            bundles[spec.factory] = spec.factory(env)
        built = bundles[spec.factory][spec.name]
        built.cache_function = never_cache
        tools.append(built)
    return tools
