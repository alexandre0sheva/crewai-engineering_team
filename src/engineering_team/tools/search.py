"""Searching and finding files: the code behind Search Project Files and Find Files."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.scope import compile_glob
from engineering_team.tools.support import ToolError, human_size
from engineering_team.tools.workspace import ProjectWorkspace

MAX_LINE_CHARS = 240
MAX_CONTEXT_LINES = 5
MAX_RESULTS_LIMIT = 200
CASE_MODES = ("smart", "sensitive", "insensitive")


def _globs(patterns: Sequence[str] | None) -> list[re.Pattern[str]]:
    return [compile_glob(pattern) for pattern in patterns or [] if pattern.strip()]


def _clip(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " ..."


def search_files(
    workspace: ProjectWorkspace,
    pattern: str,
    *,
    path: str = ".",
    regex: bool = False,
    case: str = "smart",
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    context: int = 0,
    max_results: int = 50,
) -> str:
    """Search text files under ``path``; counts first, then ``file:line: text`` matches."""

    if not pattern:
        raise ToolError("pattern cannot be empty.")
    if case not in CASE_MODES:
        raise ToolError(f"case must be one of {', '.join(CASE_MODES)}.")
    try:
        compiled = re.compile(
            pattern if regex else re.escape(pattern),
            0 if case == "sensitive" or (case == "smart" and pattern != pattern.lower()) else re.I,
        )
    except re.error as exc:
        raise ToolError(
            f"Invalid regular expression ({exc}). Escape special characters or pass regex=false "
            "for a literal search."
        ) from exc
    base = workspace.resolve(path, must_exist=True)
    context = max(0, min(int(context), MAX_CONTEXT_LINES))
    limit = max(1, min(int(max_results), MAX_RESULTS_LIMIT))
    include_globs, exclude_globs = _globs(include), _globs(exclude)

    files = [base] if base.is_file() else iter_files(workspace, base)
    matches = 0
    files_with_matches = 0
    skipped = 0
    stopped = False
    blocks: list[str] = []
    for file in files:
        relative = workspace.relative_name(file)
        if include_globs and not any(glob.fullmatch(relative) for glob in include_globs):
            continue
        if any(glob.fullmatch(relative) for glob in exclude_globs):
            continue
        text = read_text_or_none(file)
        if text is None:
            skipped += 1
            continue
        lines = text.splitlines()
        hits = [number for number, line in enumerate(lines, 1) if compiled.search(line)]
        if not hits:
            continue
        files_with_matches += 1
        room = limit - matches
        if len(hits) > room:
            hits, stopped = hits[:room], True
        matches += len(hits)
        blocks.append(_render_file(relative, lines, hits, context))
        if stopped:
            break

    if not matches:
        extra = f" ({skipped} binary or oversized files skipped)" if skipped else ""
        return (
            f"0 matches for {pattern!r} in {path}{extra}. Try case=insensitive, a shorter "
            "pattern, regex=true, or a different path."
        )
    header = f"{matches} match(es) in {files_with_matches} file(s) for {pattern!r}"
    if stopped:
        header += f" (stopped at max_results={limit}; narrow with path/include or raise it)"
    return header + "\n" + "\n".join(blocks)


def _render_file(relative: str, lines: list[str], hits: list[int], context: int) -> str:
    shown: dict[int, bool] = {}  # line number -> is a match
    for number in hits:
        for around in range(max(1, number - context), min(len(lines), number + context) + 1):
            shown.setdefault(around, False)
        shown[number] = True
    out: list[str] = []
    previous = 0
    for number in sorted(shown):
        if previous and number > previous + 1:
            out.append("--")
        separator = ":" if shown[number] else "-"
        out.append(f"{relative}{separator}{number}{separator} {_clip(lines[number - 1])}")
        previous = number
    return "\n".join(out)


def find_files(
    workspace: ProjectWorkspace,
    pattern: str,
    *,
    path: str = ".",
    sort: str = "name",
    max_results: int = 100,
) -> str:
    """List files whose path matches a glob (``*.py``, ``src/**/test_*.py``)."""

    if not pattern.strip():
        raise ToolError("pattern cannot be empty; use a glob such as '*.py' or 'src/**/*.ts'.")
    if sort not in ("name", "recent"):
        raise ToolError("sort must be 'name' or 'recent'.")
    glob = compile_glob(pattern)
    base = workspace.resolve(path, must_exist=True)
    if not base.is_dir():
        raise ToolError(f"Not a directory: {path}. Pass a directory to search below.")
    found: list[tuple[str, Path]] = []
    for file in iter_files(workspace, base, rules=IgnoreRules.for_workspace(workspace)):
        relative = workspace.relative_name(file)
        if glob.fullmatch(relative):
            found.append((relative, file))
    if sort == "recent":
        found.sort(key=lambda item: (-item[1].stat().st_mtime, item[0]))
    limit = max(1, min(int(max_results), 500))
    if not found:
        return (
            f"0 files match {pattern!r} under {path}. Try a broader glob such as '**/*{pattern}*'."
        )
    rows = []
    for relative, file in found[:limit]:
        stat = file.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        rows.append(f"{relative}  ({human_size(stat.st_size)}, {modified})")
    header = f"{len(found)} file(s) match {pattern!r} (sorted by {sort})"
    if len(found) > limit:
        header += f"; showing {limit}, narrow the glob or raise max_results"
    return header + "\n" + "\n".join(rows)
