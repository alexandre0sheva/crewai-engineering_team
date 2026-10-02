"""Workspace Changes: what this run added, modified, or deleted, as a compact diff."""

from __future__ import annotations

import difflib
import hashlib
from typing import TYPE_CHECKING

from engineering_team.tools.ignore import IgnoreRules, iter_files, looks_binary
from engineering_team.tools.workspace import ProjectWorkspace

if TYPE_CHECKING:
    from engineering_team.runtime.snapshot import Snapshot


def _current_state(workspace: ProjectWorkspace) -> dict[str, tuple[str, str | None]]:
    state: dict[str, tuple[str, str | None]] = {}
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        text = None if looks_binary(path) else data.decode("utf-8", errors="replace")
        state[workspace.relative_name(path)] = (hashlib.sha256(data).hexdigest(), text)
    return state


def workspace_changes(
    workspace: ProjectWorkspace,
    baseline: Snapshot,
    *,
    path: str = ".",
    max_diff_lines: int = 200,
    context: int = 2,
) -> str:
    """Compare the workspace with the run's starting snapshot (works without Git)."""

    scope = workspace.relative_name(workspace.resolve(path, must_exist=True))
    prefix = "" if scope == "." else scope.rstrip("/") + "/"

    def in_scope(name: str) -> bool:
        return not prefix or name == scope or name.startswith(prefix)

    current = _current_state(workspace)
    before = {name: state for name, state in baseline.files.items() if in_scope(name)}
    after = {name: state for name, state in current.items() if in_scope(name)}
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(
        name for name in set(before) & set(after) if before[name].sha256 != after[name][0]
    )
    if not (added or deleted or modified):
        return f"No changes since the run started (under {path})."

    limit = max(10, min(int(max_diff_lines), 1000))
    context = max(0, min(int(context), 10))
    out = [f"{len(added)} added, {len(modified)} modified, {len(deleted)} deleted (under {path})"]
    out += [f"A {name}" for name in added]
    out += [f"M {name}" for name in modified]
    out += [f"D {name}" for name in deleted]

    diff_lines: list[str] = []
    for name in [*added, *modified, *deleted]:
        old_text = before[name].text if name in before else ""
        new_text = after[name][1] if name in after else ""
        if old_text is None or new_text is None:
            diff_lines.append(f"--- {name}: binary, oversized, or not stored; no text diff")
            continue
        diff_lines.extend(
            difflib.unified_diff(
                old_text.splitlines(),
                new_text.splitlines(),
                fromfile=f"a/{name}" if name in before else "/dev/null",
                tofile=f"b/{name}" if name in after else "/dev/null",
                lineterm="",
                n=context,
            )
        )
    if diff_lines:
        out.append("")
        out.extend(diff_lines[:limit])
        if len(diff_lines) > limit:
            out.append(
                f"... diff truncated at {limit} of {len(diff_lines)} lines; pass a narrower "
                "path or raise max_diff_lines."
            )
    return "\n".join(out)
