"""Project tools: files, search, and commands."""

from __future__ import annotations

from engineering_team.tools.command_tools import make_command_tools
from engineering_team.tools.read_tools import make_read_tools
from engineering_team.tools.search_tools import make_search_tools
from engineering_team.tools.spec import ToolSpec, _spec
from engineering_team.tools.write_tools import make_write_tools

PROJECT_SPECS: tuple[ToolSpec, ...] = (
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
