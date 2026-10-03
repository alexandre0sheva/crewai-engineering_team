"""The tool catalogue: every agent tool, its group, and how a run's tools are assembled.

``docs/TOOLS.md`` lists the same tools; a test keeps the two in step. New tool tasks add a
``ToolSpec`` here and a row there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from engineering_team.tools import browser_tools
from engineering_team.tools.board_tools import make_board_tools
from engineering_team.tools.browser_tools import make_browser_tools
from engineering_team.tools.codeintel_tools import make_codeintel_tools
from engineering_team.tools.command_tools import make_command_tools
from engineering_team.tools.dev_tools import make_dev_tools
from engineering_team.tools.human_tools import make_human_tools
from engineering_team.tools.knowledge_tools import make_knowledge_tools
from engineering_team.tools.network_tools import make_network_tools
from engineering_team.tools.notes_tools import make_notes_tools
from engineering_team.tools.process_tools import make_process_tools
from engineering_team.tools.read_tools import make_read_tools
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.search_tools import make_search_tools
from engineering_team.tools.sqlite_tools import make_sqlite_tools
from engineering_team.tools.support import ToolEnv, never_cache
from engineering_team.tools.web_tools import make_web_tools
from engineering_team.tools.write_tools import make_write_tools

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import Settings

# ``factory`` builds every tool of one module for a run, keyed by tool name; ``build_tools``
# calls each distinct factory once per run and picks the tools it needs.
ToolFactory = Callable[[ToolEnv], Mapping[str, BaseTool]]

# The groups that work on the project's files and commands; the rest coordinate the team.
PROJECT_GROUPS = ("fs_read", "search", "fs_write", "command")
GROUPS = (
    *PROJECT_GROUPS,
    "dev",
    "code_intel",
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
    _spec(
        "Run Tests",
        "dev",
        make_dev_tools,
        "Run the project's tests (framework detected); failing tests with file, line, message.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Rerun Failed Tests",
        "dev",
        make_dev_tools,
        "Run again exactly the tests that failed last time.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Run Single Test",
        "dev",
        make_dev_tools,
        "Run one test by the id a report lists.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Run Linter",
        "dev",
        make_dev_tools,
        "Lint with the detected linter; diagnostics as file, line, rule, message.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Type Check",
        "dev",
        make_dev_tools,
        "Type-check with mypy, pyright, tsc, go vet, or cargo check.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Format Code",
        "dev",
        make_dev_tools,
        "Check or apply formatting (ruff, black, prettier, gofmt, rustfmt).",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Build Project",
        "dev",
        make_dev_tools,
        "Run the detected build; compile errors as diagnostics.",
        read_only=False,
        gate=True,
    ),
    _spec(
        "Coverage Report",
        "dev",
        make_dev_tools,
        "Tests under coverage: total, least-covered files, uncovered lines of one file.",
        read_only=False,
        gate=True,
    ),
    ToolSpec(
        "Install Dependencies",
        "dev",
        False,
        True,
        True,
        "Install the project's declared dependencies (needs the network; setup phase).",
        make_dev_tools,
    ),
    ToolSpec(
        "Dependency Audit",
        "dev",
        False,
        True,
        True,
        "Audit dependencies for known vulnerabilities; unavailable if no audit tool exists.",
        make_dev_tools,
    ),
    _spec(
        "Find Symbol",
        "code_intel",
        make_codeintel_tools,
        "Where a class, function, method, or type is defined, by name and kind.",
        read_only=True,
    ),
    _spec(
        "Show Symbol",
        "code_intel",
        make_codeintel_tools,
        "One definition's full body with numbered lines and context.",
        read_only=True,
    ),
    _spec(
        "Find References",
        "code_intel",
        make_codeintel_tools,
        "Every use of a name, classified as definition, import, call, or other.",
        read_only=True,
    ),
    _spec(
        "Who Imports",
        "code_intel",
        make_codeintel_tools,
        "Reverse dependencies of a file or module, optionally transitive.",
        read_only=True,
    ),
    _spec(
        "Imports Of",
        "code_intel",
        make_codeintel_tools,
        "What a file imports, split into project files and external packages.",
        read_only=True,
    ),
    _spec(
        "Find Related Tests",
        "code_intel",
        make_codeintel_tools,
        "Tests that import, are named after, or mention a file or symbol.",
        read_only=True,
    ),
    _spec(
        "Find TODOs",
        "code_intel",
        make_codeintel_tools,
        "TODO/FIXME/HACK comments with author and age when Git knows them.",
        read_only=True,
    ),
    _spec(
        "Hotspots",
        "code_intel",
        make_codeintel_tools,
        "Files ranked by Git churn times a complexity proxy; needs Git history.",
        read_only=True,
    ),
    _spec(
        "Inspect Dependencies",
        "code_intel",
        make_codeintel_tools,
        "Declared and locked dependencies from the project's manifests.",
        read_only=True,
    ),
    _spec(
        "Search Docs",
        "knowledge",
        make_knowledge_tools,
        "BM25 search over project docs, context directories, and pages fetched this run.",
        read_only=True,
    ),
    ToolSpec(
        "Web Search",
        "web",
        True,
        True,
        False,
        "Web search (Serper, Brave, or Tavily): titles, URLs, snippets; opt-in.",
        make_web_tools,
    ),
    ToolSpec(
        "Fetch URL",
        "web",
        True,
        True,
        False,
        "Read one public web page as Markdown; private addresses refused; opt-in.",
        make_web_tools,
    ),
    ToolSpec(
        "Package Info",
        "web",
        True,
        True,
        False,
        "Latest version, license, and deprecation flags from PyPI, npm, crates.io, or Go.",
        make_web_tools,
    ),
    _spec(
        "Browser Open",
        "browser",
        make_browser_tools,
        "Open a page of the app (this run's localhost ports) in a headless browser.",
        read_only=True,
    ),
    _spec(
        "Browser Snapshot",
        "browser",
        make_browser_tools,
        "The page as an accessibility tree with element refs: how an agent sees it.",
        read_only=True,
    ),
    _spec(
        "Browser Screenshot",
        "browser",
        make_browser_tools,
        "Save a PNG of the page or one element as a run artifact.",
        read_only=True,
    ),
    _spec(
        "Browser Click",
        "browser",
        make_browser_tools,
        "Click an element by its snapshot ref.",
        read_only=True,
    ),
    _spec(
        "Browser Type",
        "browser",
        make_browser_tools,
        "Type into an input by its snapshot ref; optionally press Enter.",
        read_only=True,
    ),
    _spec(
        "Browser Select",
        "browser",
        make_browser_tools,
        "Choose an option of a select by its snapshot ref.",
        read_only=True,
    ),
    _spec(
        "Browser Press Key",
        "browser",
        make_browser_tools,
        "Press a key or shortcut on the page or an element.",
        read_only=True,
    ),
    _spec(
        "Browser Wait For",
        "browser",
        make_browser_tools,
        "Wait for text, a load state, or a short pause.",
        read_only=True,
    ),
    _spec(
        "Browser Console & Errors",
        "browser",
        make_browser_tools,
        "Console output, uncaught errors, failed requests, and refused requests.",
        read_only=True,
    ),
    _spec(
        "Set Viewport",
        "browser",
        make_browser_tools,
        "Resize to desktop, tablet, mobile, or a custom size.",
        read_only=True,
    ),
    _spec(
        "Accessibility Check",
        "browser",
        make_browser_tools,
        "Heuristic accessibility findings on the open page.",
        read_only=True,
    ),
    _spec(
        "Browser Close",
        "browser",
        make_browser_tools,
        "Close your browser context.",
        read_only=True,
    ),
    _spec(
        "Start Background Process",
        "runtime",
        make_process_tools,
        "Start a server or watcher in the background; wait for a port, URL, log line, or delay.",
        read_only=False,
    ),
    _spec(
        "List Processes",
        "runtime",
        make_process_tools,
        "This run's background processes with state, age, exit code, and ports.",
        read_only=True,
    ),
    _spec(
        "Read Process Logs",
        "runtime",
        make_process_tools,
        "Tail, grep, or read-from-offset a background process's output.",
        read_only=True,
    ),
    _spec(
        "Stop Process",
        "runtime",
        make_process_tools,
        "Stop a background process (whole process group) and show its last output.",
        read_only=False,
    ),
    _spec(
        "Wait For Service",
        "runtime",
        make_process_tools,
        "Wait for a local port or URL to answer, with a timeout.",
        read_only=True,
    ),
    _spec(
        "HTTP Request",
        "runtime",
        make_network_tools,
        "Call this run's own servers (loopback, own ports; others only via the allowlist).",
        read_only=False,
    ),
    _spec(
        "Check Port",
        "runtime",
        make_network_tools,
        "Report whether a local port is free, in use, or accepting connections.",
        read_only=True,
    ),
    _spec(
        "Find Free Port",
        "runtime",
        make_network_tools,
        "Reserve a free local port, distinct for every caller in the run.",
        read_only=True,
    ),
    _spec(
        "Environment Info",
        "runtime",
        make_network_tools,
        "OS, CPUs, memory, tool versions on PATH, and missing allowlisted executables.",
        read_only=False,
    ),
    _spec(
        "Query SQLite",
        "runtime",
        make_sqlite_tools,
        "Run one read-only SELECT against a SQLite file; rows as a table.",
        read_only=True,
    ),
    _spec(
        "Inspect Database Schema",
        "runtime",
        make_sqlite_tools,
        "Tables, columns, indexes, and row counts of a SQLite file.",
        read_only=True,
    ),
    _spec(
        "List Board Cards",
        "board",
        make_board_tools,
        "Compact table of cards with overall progress; filter by status, kind, or mine.",
        read_only=True,
    ),
    _spec(
        "Get Board Card",
        "board",
        make_board_tools,
        "One card in full: dependencies, comments (including the human's), history.",
        read_only=True,
    ),
    _spec(
        "Add Subtask",
        "board",
        make_board_tools,
        "Add a subtask under one of your own cards.",
        read_only=True,
    ),
    _spec(
        "Move Card",
        "board",
        make_board_tools,
        "Start or hand over your own card (never done or failed; the controller decides).",
        read_only=True,
    ),
    _spec(
        "Block Card",
        "board",
        make_board_tools,
        "Flag your own card as blocked, with a reason.",
        read_only=True,
    ),
    _spec(
        "Unblock Card",
        "board",
        make_board_tools,
        "Resume your own blocked card.",
        read_only=True,
    ),
    _spec(
        "Comment On Card",
        "board",
        make_board_tools,
        "Comment on any card; never changes its status.",
        read_only=True,
    ),
    _spec(
        "Report Progress",
        "board",
        make_board_tools,
        "One-line status on your own card, shown in the activity feed.",
        read_only=True,
    ),
    _spec(
        "Write Note",
        "notes",
        make_notes_tools,
        "Save or append to a shared note for the other agents of this run.",
        read_only=True,
    ),
    _spec(
        "Read Note",
        "notes",
        make_notes_tools,
        "Read a shared note, or the project's decision log with key 'decisions'.",
        read_only=True,
    ),
    _spec(
        "List Notes",
        "notes",
        make_notes_tools,
        "Shared notes of this run with sizes.",
        read_only=True,
    ),
    _spec(
        "Log Decision",
        "notes",
        make_notes_tools,
        "Append a dated decision to the project's append-only decision log.",
        read_only=True,
    ),
    _spec(
        "Ask Human",
        "human",
        make_human_tools,
        "Ask the person running the team a question and wait, with a timeout.",
        read_only=True,
    ),
)


def web_tools_allowed(settings: Settings, agent: str | None) -> bool:
    """Whether the ``web`` group exists for this teammate: enabled, and in ``web.roles`` if set."""

    if not settings.web.enabled:
        return False
    roles = settings.web.roles
    return not roles or (agent or "").strip().lower().replace(" ", "_") in {
        role.replace(" ", "_") for role in roles
    }


def build_tools(
    ctx: RunContext,
    *,
    groups: Iterable[str] | None = None,
    write_scope: WriteScope | None = None,
    read_only: bool = False,
    agent: str | None = None,
    lane: int | str | None = None,
) -> list[BaseTool]:
    """Build one run's tools, in catalogue order.

    ``groups`` selects tool groups (default: all). ``write_scope`` limits which paths the
    write tools may change; reads stay unrestricted. ``read_only`` leaves out every tool
    that changes files or runs commands (the board, notes, and human tools only change the
    run's own state, so read-only teammates keep them). The ``web`` group is left out unless
    ``web.enabled`` is set (and, with ``web.roles``, the teammate is listed). ``agent`` is the
    teammate the tools belong to: the board and notes tools act, comment, and are logged as
    that teammate. ``lane`` is the parallel lane the tools work in: it tags their events and
    keeps the teammate's browser session and processes apart from a same-role teammate's.
    """

    wanted = tuple(GROUPS if groups is None else groups)
    web_allowed = web_tools_allowed(ctx.settings, agent)
    browser_present = browser_tools.available()
    unknown = sorted(set(wanted) - set(GROUPS))
    if unknown:
        raise ValueError(
            f"Unknown tool group(s): {', '.join(unknown)}. Known: {', '.join(GROUPS)}."
        )
    env = ToolEnv(ctx, write_scope, agent, lane)
    bundles: dict[ToolFactory, Mapping[str, BaseTool]] = {}
    tools: list[BaseTool] = []
    for spec in CATALOGUE:
        if spec.group not in wanted or (read_only and not spec.read_only):
            continue
        if spec.group == "web" and not web_allowed:
            continue  # off unless enabled: a disabled run has no network tool registered at all
        if spec.group == "browser" and not browser_present:
            continue  # the optional Playwright extra is not installed
        if spec.factory not in bundles:
            bundles[spec.factory] = spec.factory(env)
        built = bundles[spec.factory][spec.name]
        built.cache_function = never_cache
        tools.append(built)
    return tools
