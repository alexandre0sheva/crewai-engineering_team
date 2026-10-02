"""Find TODOs: tagged comments, with the author and age of the line when Git knows them."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.codeintel.git import DAY_SECONDS, GitHistory, GitUnavailable
from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.workspace import ProjectWorkspace

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

DEFAULT_TAGS = ("TODO", "FIXME", "HACK", "XXX")
MAX_BLAME_FILES = 20
MAX_BLAME_LINES = 50
MAX_TODO_FILE_BYTES = 500_000
_CLOSERS = re.compile(r"\s*(?:\*/|-->)\s*$")


@dataclass(frozen=True)
class Todo:
    path: str
    line: int
    tag: str
    owner: str | None  # the "(name)" written after the tag, if any
    text: str


@dataclass(frozen=True)
class TodoInfo:
    todo: Todo
    author: str | None  # from blame; "uncommitted" for lines not committed; None when unknown
    age_days: float | None


def scan_todos(
    workspace: ProjectWorkspace, *, tags: tuple[str, ...] = DEFAULT_TAGS, path_prefix: str = ""
) -> list[Todo]:
    """Tagged comments in every text file (``.gitignore`` and heavy directories honoured)."""

    if not tags:
        return []
    pattern = re.compile(
        r"(?:#|//|/\*|<!--|\*|--)[^\w\n]*(" + "|".join(map(re.escape, tags)) + r")\b"
        r"(?:\(([^)\n]*)\))?[:\s-]*(.*)"
    )
    base = workspace.resolve(path_prefix or ".", must_exist=True)
    found: list[Todo] = []
    for path in (
        [base]
        if base.is_file()
        else iter_files(workspace, base, rules=IgnoreRules.for_workspace(workspace))
    ):
        text = read_text_or_none(path, limit=MAX_TODO_FILE_BYTES)
        if text is None or not any(tag in text for tag in tags):
            continue
        relative = workspace.relative_name(path)
        for number, line in enumerate(text.splitlines(), start=1):
            if match := pattern.search(line):
                owner = (match.group(2) or "").strip() or None
                found.append(
                    Todo(
                        relative,
                        number,
                        match.group(1),
                        owner,
                        _CLOSERS.sub("", match.group(3)).strip(),
                    )
                )
    return found


def with_blame(ctx: RunContext, todos: list[Todo], now: float | None) -> list[TodoInfo]:
    """Attach the author and age of each line (best effort: first files only, no Git is fine)."""

    history = GitHistory(ctx)
    try:
        history.ensure_repository()
    except GitUnavailable:
        return [TodoInfo(todo, None, None) for todo in todos]
    moment = time.time() if now is None else now
    by_file: dict[str, list[int]] = {}
    for todo in todos:
        by_file.setdefault(todo.path, []).append(todo.line)
    blamed = {
        path: history.blame(path, lines[:MAX_BLAME_LINES])
        for path, lines in list(by_file.items())[:MAX_BLAME_FILES]
    }
    result: list[TodoInfo] = []
    for todo in todos:
        info = blamed.get(todo.path, {}).get(todo.line)
        if info is None:
            result.append(TodoInfo(todo, None, None))
        else:
            age = None if info.when is None else max(0.0, (moment - info.when) / DAY_SECONDS)
            result.append(TodoInfo(todo, info.author, age))
    return result


def annotate(
    ctx: RunContext,
    todos: list[Todo],
    *,
    limit: int,
    use_git: bool = True,
    now: float | None = None,
) -> tuple[list[TodoInfo], str | None]:
    """Todos with blame data for the first ``limit``, and a note saying why it is missing."""

    def plain(items: list[Todo]) -> list[TodoInfo]:
        return [TodoInfo(todo, None, None) for todo in items]

    if not use_git or not todos:
        return plain(todos), None
    try:
        GitHistory(ctx).ensure_repository()
    except GitUnavailable as exc:
        return plain(todos), f"Author and age are unknown: no Git history ({exc})."
    return [*with_blame(ctx, todos[:limit], now), *plain(todos[limit:])], None


def age_label(days: float | None) -> str:
    if days is None:
        return ""
    whole = int(days)
    if whole < 1:
        return "today"
    if whole < 60:
        return f"{whole}d"
    if whole < 365:
        return f"{whole // 30}mo"
    return f"{whole // 365}y"


def format_todos(items: list[TodoInfo], *, limit: int, git_note: str | None) -> str:
    if not items:
        return "No TODO/FIXME/HACK comments found."
    counts = Counter(item.todo.tag for item in items)
    files = len({item.todo.path for item in items})
    summary = ", ".join(f"{count} {tag}" for tag, count in sorted(counts.items()))
    lines = [f"{len(items)} item(s) in {files} file(s): {summary}"]
    for item in items[:limit]:
        todo = item.todo
        owner = f"({todo.owner})" if todo.owner else ""
        who = ", ".join(part for part in (item.author, age_label(item.age_days)) if part)
        lines.append(
            f"{todo.path}:{todo.line} {todo.tag}{owner} {todo.text}" + (f"  ({who})" if who else "")
        )
    if len(items) > limit:
        lines.append(f"... {len(items) - limit} more; narrow with path or tags.")
    if git_note:
        lines.append(git_note)
    return "\n".join(lines)
