"""Group ``fs_read``: listing, reading, and inspecting files."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from crewai.tools import BaseTool, tool

from engineering_team.tools.ignore import IgnoreRules, iter_files, looks_binary
from engineering_team.tools.support import (
    ToolEnv,
    ToolError,
    bounded,
    human_size,
    missing_path_error,
    numbered,
)
from engineering_team.tools.symbols import supports
from engineering_team.tools.workspace import MAX_READ_BYTES, ProjectWorkspace

MAX_RANGE_FILE_BYTES = 5_000_000
DEFAULT_RANGE_LINES = 200
MAX_RANGE_LINES = 600
MAX_MANY_FILES = 20
MAX_MANY_CHARS = 60_000
MAX_TREE_ENTRIES = 400
RANGE_SPEC = re.compile(r"^(?P<path>.+?):(?P<start>\d+)-(?P<end>\d+)$")


def _existing_file(workspace: ProjectWorkspace, path: str) -> Path:
    resolved = workspace.resolve(path, readonly=True)
    if not resolved.exists():
        raise missing_path_error(workspace, path)
    if resolved.is_dir():
        raise ToolError(f"{path} is a directory. Use Project Tree or List Project Files.")
    return resolved


def _range(workspace: ProjectWorkspace, path: str, start: int, count: int) -> tuple[str, str]:
    """(header, numbered body) for ``count`` lines of ``path`` from 1-based line ``start``."""

    resolved = _existing_file(workspace, path)
    size = resolved.stat().st_size
    if size > MAX_RANGE_FILE_BYTES:
        raise ToolError(
            f"{path} is {human_size(size)}, over the {human_size(MAX_RANGE_FILE_BYTES)} read "
            "limit. Use Search Project Files to find the part you need."
        )
    data = resolved.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path} is a binary file; the text tools cannot read it.")
    lines = data.decode("utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, int(start))
    count = max(1, min(int(count), MAX_RANGE_LINES))
    if start > max(total, 1):
        raise ToolError(f"{path} has {total} line(s); start_line={start} is past the end.")
    chunk = lines[start - 1 : start - 1 + count]
    last = start + len(chunk) - 1
    if not chunk:
        return f"{path} lines 0-0 of 0", "(empty file)"
    body = numbered(chunk, start)
    if last < total:
        body += f"\n... {total - last} more line(s); continue with start_line={last + 1}"
    return f"{path} lines {start}-{last} of {total}", body


def read_lines(workspace: ProjectWorkspace, path: str, start: int, count: int) -> str:
    header, body = _range(workspace, path, start, count)
    return f"{header}\n{body}"


def _project_tree(workspace: ProjectWorkspace, path: str, max_depth: int) -> str:
    base = workspace.resolve(path, must_exist=True)
    if not base.is_dir():
        raise ToolError(f"{path} is a file. Use File Info or Read File Range.")
    depth_limit = max(1, min(int(max_depth), 8))
    rules = IgnoreRules.for_workspace(workspace)
    files = [
        (file.relative_to(base).parts, file.stat().st_size)
        for file in iter_files(workspace, base, rules=rules)
    ]
    directories: dict[tuple[str, ...], list[int]] = {}  # dir -> [file count, bytes]
    for parts, size in files:
        for depth in range(1, len(parts)):
            entry = directories.setdefault(parts[:depth], [0, 0])
            entry[0] += 1
            entry[1] += size
    sizes = {parts: size for parts, size in files}

    lines: list[str] = []

    def children(prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
        found = {
            parts[: len(prefix) + 1]
            for parts, _ in files
            if parts[: len(prefix)] == prefix and len(parts) > len(prefix)
        }
        return sorted(found, key=lambda parts: (parts not in directories, parts[-1].lower()))

    def walk(prefix: tuple[str, ...], depth: int) -> None:
        for child in children(prefix):
            if len(lines) >= MAX_TREE_ENTRIES:
                return
            indent = "  " * depth
            if child in directories:
                count, size = directories[child]
                lines.append(f"{indent}{child[-1]}/  ({count} files, {human_size(size)})")
                if depth + 1 < depth_limit:
                    walk(child, depth + 1)
            else:
                lines.append(f"{indent}{child[-1]}  ({human_size(sizes[child])})")

    walk((), 0)
    total_size = sum(size for _, size in files)
    header = (
        f"{path}: {len(files)} file(s), {human_size(total_size)} "
        "(heavy and ignored directories omitted)"
    )
    if len(lines) >= MAX_TREE_ENTRIES:
        lines.append(
            f"... tree limited to {MAX_TREE_ENTRIES} entries; use a subdirectory or lower depth."
        )
    return header + "\n" + ("\n".join(lines) if lines else "(empty)")


def _file_info(workspace: ProjectWorkspace, path: str) -> str:
    resolved = workspace.resolve(path, must_exist=True, readonly=True)
    lexical = workspace.root / path.strip()
    stat = resolved.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    kind = "symlink" if lexical.is_symlink() else "directory" if resolved.is_dir() else "file"
    rows = [f"path: {workspace.relative_name(resolved)}", f"type: {kind}", f"modified: {modified}"]
    if resolved.is_dir():
        entries = sum(1 for _ in resolved.iterdir())
        rows.append(f"entries: {entries}")
        return "\n".join(rows)
    rows.append(f"size: {stat.st_size} bytes ({human_size(stat.st_size)})")
    if looks_binary(resolved):
        rows.append("binary: yes (the text tools cannot read it)")
    else:
        text = resolved.read_bytes()[:MAX_RANGE_FILE_BYTES].decode("utf-8", errors="replace")
        rows.append("binary: no")
        rows.append(f"lines: {len(text.splitlines())}")
        language = resolved.suffix.lstrip(".") or "none"
        outline = " (outline supported)" if supports(resolved.name) else ""
        rows.append(f"language: {language}{outline}")
    return "\n".join(rows)


def _read_many(workspace: ProjectWorkspace, files: list[str]) -> str:
    if not files:
        raise ToolError("files is empty; pass paths like 'src/app.py' or 'src/app.py:10-80'.")
    if len(files) > MAX_MANY_FILES:
        raise ToolError(f"Too many files ({len(files)}); the limit is {MAX_MANY_FILES} per call.")
    sections: list[str] = []
    used = 0
    for spec in files:
        match = RANGE_SPEC.match(spec.strip())
        if match:
            path, start, end = match["path"], int(match["start"]), int(match["end"])
        else:
            path, start, end = spec.strip(), 1, 400
        if MAX_MANY_CHARS - used <= 200:
            sections.append(f"=== {path} ===\nskipped: total size limit reached; request it alone")
            continue
        try:
            header, body = _range(workspace, path, start, max(1, end - start + 1))
        except (ToolError, OSError) as exc:
            sections.append(f"=== {path} ===\nERROR: {exc}")
            continue
        body = bounded(body, MAX_MANY_CHARS - used, hint="request a narrower path:START-END range")
        sections.append(f"=== {header} ===\n{body}")
        used += len(body)
    return "\n\n".join(sections)


def make_read_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace

    @tool("List Project Files")
    def list_project_files(path: str = ".", max_depth: int = 4) -> str:
        """List the project tree below a relative path.

        Heavy dependency and cache directories are omitted. Use this before edits
        and after scaffolding so work is based on the actual project structure.
        """

        return env.run(
            "List Project Files",
            lambda: workspace.list_files(path, max_depth),
            arguments={"path": path, "max_depth": max_depth},
        )

    @tool("Read Project File")
    def read_project_file(path: str) -> str:
        """Read one whole UTF-8 text file (up to 250 KB) by relative path.

        For big files or just a section, use Read File Range; to find where something
        is, use Search Project Files.
        """

        def operation() -> str:
            resolved = _existing_file(workspace, path)
            if resolved.stat().st_size > MAX_READ_BYTES:
                raise ToolError(
                    f"{path} is {human_size(resolved.stat().st_size)}, over the "
                    f"{MAX_READ_BYTES}-byte whole-file limit. Use Read File Range "
                    "(start_line, max_lines) instead."
                )
            return workspace.read_file(path)

        return env.run("Read Project File", operation, arguments={"path": path})

    @tool("Read File Range")
    def read_file_range(
        path: str, start_line: int = 1, max_lines: int = DEFAULT_RANGE_LINES
    ) -> str:
        """Read numbered lines of a text file from 1-based start_line (max_lines default 200).

        Use after Search Project Files or Project Outline to read just the part you
        need. Also reads full command logs ('full log:' paths). Example: path='src/app.py',
        start_line=40, max_lines=60.
        """

        return env.run(
            "Read File Range",
            lambda: read_lines(workspace, path, start_line, max_lines),
            arguments={"path": path, "start_line": start_line, "max_lines": max_lines},
        )

    @tool("Read Many Files")
    def read_many_files(files: list[str]) -> str:
        """Read several files in one call; each entry is 'path' or 'path:START-END' (1-based).

        Up to 20 files and about 60,000 characters in total; bare paths return their first 400
        lines. A bad path reports its own error without failing the others.
        Example: ['src/a.py:1-80', 'README.md'].
        """

        return env.run(
            "Read Many Files",
            lambda: _read_many(workspace, files),
            arguments={"files": files},
        )

    @tool("File Info")
    def file_info(path: str) -> str:
        """Show a path's type, size, line count, modified time, and whether it is binary.

        Cheap check before reading: use it to decide between Read Project File and Read File Range.
        """

        return env.run("File Info", lambda: _file_info(workspace, path), arguments={"path": path})

    @tool("Project Tree")
    def project_tree(path: str = ".", max_depth: int = 3) -> str:
        """Show the directory tree with file counts and sizes (depth up to 8, default 3).

        Skips dependency, build, and cache directories and honours .gitignore. Start here
        to orient in an unfamiliar project, then go deeper with a subdirectory path.
        """

        return env.run(
            "Project Tree",
            lambda: _project_tree(workspace, path, max_depth),
            arguments={"path": path, "max_depth": max_depth},
        )

    return {
        "List Project Files": list_project_files,
        "Read Project File": read_project_file,
        "Read File Range": read_file_range,
        "Read Many Files": read_many_files,
        "File Info": file_info,
        "Project Tree": project_tree,
    }
