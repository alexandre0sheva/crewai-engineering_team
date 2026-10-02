"""Shared plumbing for every agent tool: guarding, scope, telemetry, bounded output.

All tools run through :meth:`ToolEnv.run`, so cancellation, the tool-call gate, write scopes,
the ``ERROR:`` convention, and ``tool.call`` events behave identically everywhere.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from engineering_team.tools.scope import WriteScope
from engineering_team.tools.workspace import ProjectWorkspace, WorkspaceError

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

CANCELLED = "ERROR: The run was cancelled. Stop working and summarize what is done."
MAX_RESULT_CHARS = 30_000
MAX_LOGGED_ARG_CHARS = 120
# Arguments that carry file content or diffs: logged as a size, never as text.
BULKY_ARGUMENTS = frozenset({"content", "old_text", "new_text", "patch", "edits"})


class ToolError(ValueError):
    """A tool call that cannot be done; the message tells the agent how to fix the call."""


def never_cache(*_args: object, **_kwargs: object) -> bool:
    return False


def bounded(text: str, limit: int = MAX_RESULT_CHARS, *, hint: str = "narrow the request") -> str:
    """Cut ``text`` to ``limit`` characters, saying so (and how to get the rest)."""

    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... output truncated at {limit} characters; {hint}."


def _loggable(arguments: Mapping[str, object]) -> dict[str, object]:
    logged: dict[str, object] = {}
    for name, value in arguments.items():
        if name in BULKY_ARGUMENTS and value is not None:
            size = len(value) if isinstance(value, str | list | tuple) else 1
            logged[name] = f"<{size} {'chars' if isinstance(value, str) else 'items'}>"
        elif isinstance(value, str) and len(value) > MAX_LOGGED_ARG_CHARS:
            logged[name] = value[:MAX_LOGGED_ARG_CHARS] + "..."
        else:
            logged[name] = value
    return logged


def _files_below(directory: Path) -> Iterator[Path]:
    """Every entry a recursive delete would remove, without following symlinks."""

    for current, directory_names, file_names in os.walk(directory):
        base = Path(current)
        yield from (base / name for name in file_names)
        yield from (base / name for name in directory_names if (base / name).is_symlink())


def scope_violation(
    workspace: ProjectWorkspace, scope: WriteScope, relative_path: str, *, deleting: bool
) -> str | None:
    """Return why ``scope`` forbids changing ``relative_path``, or ``None`` if it is allowed.

    Paths are judged where they really land, after symlink resolution. Deleting a directory
    requires every file inside it to be in scope, because the delete removes them all.
    """

    supplied = Path(relative_path.strip() or ".")
    if supplied.is_absolute() or ".." in supplied.parts:
        return None  # the workspace rejects these itself, with its own message
    try:
        if deleting and (workspace.root / supplied).is_symlink():
            parent = workspace.resolve(supplied.parent.as_posix(), must_exist=True)
            target = parent / supplied.name  # act on the link itself, never follow it
        else:
            target = workspace.resolve(relative_path)
    except WorkspaceError:
        return None
    name = workspace.relative_name(target)
    if deleting and target.is_dir() and not target.is_symlink():
        inside = [workspace.relative_name(path) for path in _files_below(target)]
        blocked = [path for path in inside if not scope.permits(path)]
        if blocked or (not inside and not scope.permits(name)):
            return scope.denial(blocked[0] if blocked else PurePosixPath(name).as_posix())
        return None
    return None if scope.permits(name) else scope.denial(name)


@dataclass(frozen=True)
class ToolEnv:
    """What a tool needs from its run, plus the common call wrapper."""

    ctx: RunContext
    write_scope: WriteScope | None = None

    @property
    def workspace(self) -> ProjectWorkspace:
        return self.ctx.workspace

    def run(
        self,
        tool: str,
        operation: Callable[[], str],
        *,
        arguments: Mapping[str, object] | None = None,
        changes: Sequence[str] = (),
        deletes: Sequence[str] = (),
    ) -> str:
        """Run one tool call and return its result string (never raises).

        Order: cancellation, the tool-call gate, write-scope checks for ``changes`` (paths
        written) and ``deletes`` (paths removed), the operation itself. Every outcome emits a
        ``tool.call`` event with redacted arguments, the duration, and whether it succeeded.
        """

        started = time.monotonic()
        result = self._guarded(tool, operation, changes, deletes)
        self.ctx.events.emit(
            "tool.call",
            tool=tool,
            args=_loggable(arguments or {}),
            duration=round(time.monotonic() - started, 4),
            ok=not result.startswith("ERROR:"),
        )
        return result

    def ensure_writable(self, paths: Sequence[str], *, deleting: bool = False) -> None:
        """Raise :class:`ToolError` if the write scope forbids changing any of ``paths``."""

        if self.write_scope is None:
            return
        for path in paths:
            violation = scope_violation(self.workspace, self.write_scope, path, deleting=deleting)
            if violation is not None:
                raise ToolError(violation)

    def _guarded(
        self,
        tool: str,
        operation: Callable[[], str],
        changes: Sequence[str],
        deletes: Sequence[str],
    ) -> str:
        if self.ctx.cancel_event.is_set():
            overspent = self.ctx.budget.tripped
            # Tell the agent *why* it must stop; a budget stop is not a user cancellation.
            return (
                f"ERROR: {overspent} Stop working and summarize what is done."
                if overspent
                else CANCELLED
            )
        if self.ctx.tool_gate is not None and (refusal := self.ctx.tool_gate(tool)) is not None:
            return f"ERROR: {refusal}"
        try:
            self.ensure_writable(changes)
            self.ensure_writable(deletes, deleting=True)
            return operation()
        except (OSError, UnicodeError, WorkspaceError, ToolError) as exc:
            return f"ERROR: {exc}"


def numbered(lines: Sequence[str], first: int) -> str:
    """Lines prefixed with right-aligned 1-based numbers and a tab, like ``cat -n``."""

    width = len(str(first + len(lines)))
    return "\n".join(f"{first + offset:>{width}}\t{line}" for offset, line in enumerate(lines))


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"  # unreachable; keeps the type checker satisfied


def similar_paths(workspace: ProjectWorkspace, relative_path: str, limit: int = 3) -> list[str]:
    """Existing paths whose names resemble ``relative_path`` (for "did you mean" hints)."""

    import difflib

    from engineering_team.tools.ignore import iter_files

    wanted = Path(relative_path).name
    candidates = {workspace.relative_name(path): path.name for path in iter_files(workspace)}
    close = difflib.get_close_matches(wanted, set(candidates.values()), n=limit, cutoff=0.6)
    return sorted(path for path, name in candidates.items() if name in close)[:limit]


def missing_path_error(workspace: ProjectWorkspace, relative_path: str) -> ToolError:
    hint = similar_paths(workspace, relative_path)
    suffix = f" Did you mean: {', '.join(hint)}?" if hint else " Use Find Files to locate it."
    return ToolError(f"File not found: {relative_path}.{suffix}")
