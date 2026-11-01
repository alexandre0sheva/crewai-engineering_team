"""The attempts' workspaces: a private copy each, its change as a patch, and the chosen one
carried back to the run's workspace.

An attempt works on a copy of the run's workspace as it was when the attempts started, made
under ``.engineering-team/attempts/<run-id>/<label>`` (the controller's state directory, which
agents and snapshots never see). The copy leaves out what ``isolation.py`` leaves out of a copy
(the controller's state, caches, virtual environments) except the request's context documents
and the repository memory, and it leaves out a ``.git`` *file*: a Git worktree's link back to the
repository it belongs to, which an attempt must never commit to. A ``.git`` directory is copied,
so the attempt has a repository of its own.

What an attempt changed is the difference between the starting snapshot and its tree (the same
non-ignored files a snapshot covers), as a ``git diff``-style patch.
"""

from __future__ import annotations

import contextlib
import difflib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.modes.isolation import COPY_SKIP, MAX_COPY_BYTES
from engineering_team.runtime.snapshot import Snapshot
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace

if TYPE_CHECKING:
    from engineering_team.settings import Settings

ATTEMPTS_WORKSPACES = Path(CONTROLLER_DIRECTORY) / "attempts"
# What an attempt's copy keeps of the controller's state directory.
CONTROLLER_KEPT = ("context", "memory")


class AttemptCopyError(ValueError):
    """The workspace cannot be copied for the attempts (too large, or the copy failed)."""


@dataclass(frozen=True)
class TreeDiff:
    """What an attempt changed: ``changes`` maps each path to ``A``, ``M`` or ``D``."""

    changes: dict[str, str] = field(default_factory=dict)
    patch: str = ""
    lines: int = 0  # added plus removed lines; a file without a text diff counts as one

    @property
    def files(self) -> int:
        return len(self.changes)


def copies_root(workspace: ProjectWorkspace, run_id: str) -> Path:
    return workspace.root / ATTEMPTS_WORKSPACES / run_id


def check_size(workspace: ProjectWorkspace, count: int) -> None:
    """Refuse a workspace too large to copy ``count`` times (``isolation.MAX_COPY_BYTES``)."""

    size = _tree_bytes(workspace.root)
    if size > MAX_COPY_BYTES:
        raise AttemptCopyError(
            f"The workspace is {size / 1024**3:.1f} GiB, too large to copy for {count} attempts "
            f"(limit {MAX_COPY_BYTES // 1024**3} GiB per copy). Run without --attempts."
        )


def make_copy(workspace: ProjectWorkspace, target: Path, settings: Settings) -> ProjectWorkspace:
    """Copy ``workspace`` to ``target`` (replacing an older copy) and open it."""

    shutil.rmtree(target, ignore_errors=True)
    try:
        shutil.copytree(workspace.root, target, symlinks=True, ignore=_skip)
        for name in CONTROLLER_KEPT:
            source = workspace.root / CONTROLLER_DIRECTORY / name
            if source.is_dir():
                shutil.copytree(
                    source, target / CONTROLLER_DIRECTORY / name, symlinks=True, dirs_exist_ok=True
                )
    except (OSError, shutil.Error) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise AttemptCopyError(f"Copying the workspace to {target} failed: {exc}") from exc
    return ProjectWorkspace.create(
        target,
        extra_commands=settings.command_allowlist,
        env_passthrough=settings.subprocess_env_allowlist,
    )


def _skip(directory: str, names: list[str]) -> set[str]:
    skipped = {name for name in names if name in COPY_SKIP}
    if ".git" in names and os.path.isfile(os.path.join(directory, ".git")):
        skipped.add(".git")  # a worktree's link to its repository: never shared with a copy
    return skipped


def _tree_bytes(directory: Path) -> int:
    total = 0
    for current, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in COPY_SKIP]
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def tree_diff(start: Snapshot, workspace: ProjectWorkspace) -> TreeDiff:
    """What ``workspace`` changed since ``start``, with a ``git diff``-style patch."""

    after = Snapshot.take(workspace)
    before_files, after_files = start.files, after.files
    changes: dict[str, str] = {}
    for name in sorted(set(before_files) | set(after_files)):
        if name not in before_files:
            changes[name] = "A"
        elif name not in after_files:
            changes[name] = "D"
        elif before_files[name].sha256 != after_files[name].sha256:
            changes[name] = "M"
    out: list[str] = []
    counted = 0
    for name, kind in changes.items():
        old = before_files[name].text if name in before_files else ""
        new = after_files[name].text if name in after_files else ""
        out.append(f"diff --git a/{name} b/{name}")
        if kind == "A":
            out.append("new file mode 100644")
        elif kind == "D":
            out.append("deleted file mode 100644")
        if old is None or new is None:
            out.append(f"Binary files a/{name} and b/{name} differ")
            counted += 1
            continue
        body = list(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile="/dev/null" if kind == "A" else f"a/{name}",
                tofile="/dev/null" if kind == "D" else f"b/{name}",
                lineterm="",
            )
        )
        out.extend(body)
        counted += sum(
            1
            for line in body[2:]
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
    patch = "\n".join(out) + ("\n" if out else "")
    return TreeDiff(changes, patch, counted)


def apply_changes(diff: TreeDiff, source: ProjectWorkspace, target: ProjectWorkspace) -> None:
    """Make ``target`` hold ``source``'s version of every path ``diff`` changed (whole files,
    binary ones too)."""

    for name, kind in diff.changes.items():
        destination = target.root / name
        if kind == "D":
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.root / name, destination)


def remove_copies(workspace: ProjectWorkspace, run_id: str) -> None:
    root = copies_root(workspace, run_id)
    shutil.rmtree(root, ignore_errors=True)
    with contextlib.suppress(OSError):  # .engineering-team/attempts, unless another run uses it
        root.parent.rmdir()
