"""Isolation policies: how the team gets a place to work on a project that is not its own.

The user's checkout is never edited blindly. :func:`isolate` picks one of three ways to give the
team a workspace and returns an :class:`Isolation` saying where, and on which branch:

``branch``
    A Git repository with a clean tree: the team works in the checkout itself, on a new branch
    ``engineering-team/<run-id>-<slug>`` made from the current commit. The files on disk are
    untouched until the team writes; your branch is untouched for good.
``worktree``
    The default when the tree is dirty (or when asked for): ``git worktree add`` creates
    ``.engineering-team/worktrees/<run-id>`` on the same new branch, so your working copy,
    including uncommitted changes, is not touched at all. The team starts from the last commit.
``copy``
    A directory that is not a repository: it is copied to ``<workspace root>/<name>`` and the
    team works on the copy. With ``init_git`` the copy becomes a repository whose first commit
    is the imported state, so the team's work can be diffed against it.

A dirty tree is never worked on in place unless ``allow_dirty`` says so. The controller's state
directory is kept out of Git through ``.git/info/exclude``, never through the project's own
``.gitignore``. Nothing here pushes, fetches, or touches a remote.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from engineering_team.atomic_io import atomic_write_json
from engineering_team.git.port import GitError, GitPort
from engineering_team.modes.repo_analyzer import (
    enclosing_repository,
    standalone_git,
    status_counts,
)
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace
from engineering_team.workspaces import adopt_directory, slugify_project_name

IsolationMode = Literal["branch", "worktree", "copy"]
Requested = Literal["auto", "branch", "worktree"]

BRANCH_PREFIX = "engineering-team"
WORKTREES = Path(CONTROLLER_DIRECTORY) / "worktrees"
RECORD = Path(CONTROLLER_DIRECTORY) / "isolation.json"
MAX_COPY_BYTES = 2 * 1024**3
MAX_SLUG = 40
# Not copied into a copy-mode workspace: the controller's state, and caches and environments
# that are rebuilt (a virtual environment holds absolute paths and does not survive a copy).
COPY_SKIP = frozenset(
    {CONTROLLER_DIRECTORY, "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}
)


class IsolationError(ValueError):
    """Isolation that cannot be set up (a usage error); the message says how to fix the call."""


@dataclass(frozen=True)
class Isolation:
    """Where the team works, and how it got there."""

    mode: IsolationMode
    source: Path  # the user's directory (never written to by worktree and copy modes)
    workspace: Path  # where the team works
    branch: str | None = None  # the team's branch (branch and worktree modes)
    base_branch: str | None = None  # the branch the user was on
    base_commit: str | None = None  # the commit the team started from
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "source": str(self.source),
            "workspace": str(self.workspace),
            "branch": self.branch,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "notes": list(self.notes),
        }

    def describe(self) -> str:
        where = f"working copy at {self.workspace}"
        if self.mode == "branch":
            return f"branch '{self.branch}' in your checkout ({self.workspace})"
        if self.mode == "worktree":
            return f"branch '{self.branch}' in a separate {where}"
        return f"copy of your directory: {where}"


def isolate(
    source: str | Path,
    *,
    run_id: str,
    slug: str = "work",
    mode: Requested = "auto",
    allow_dirty: bool = False,
    init_git: bool = False,
    workspace_root: str | Path = "workspace",
    name: str | None = None,
) -> Isolation:
    """Give the team a workspace for the project at ``source`` (see the module docstring).

    ``mode`` ``auto`` picks ``branch`` for a clean repository and ``worktree`` for a dirty one
    (``allow_dirty`` keeps a dirty tree in place on a branch); asking for ``branch`` on a dirty
    tree is refused without ``allow_dirty``. A directory that is not a repository is copied.
    Raises :class:`IsolationError` for anything that cannot be done safely.
    """

    directory = Path(source).expanduser().resolve()
    if not directory.is_dir():
        raise IsolationError(f"{directory} is not a directory.")
    if directory == Path(directory.anchor) or directory == Path.home().resolve():
        raise IsolationError(f"{directory} is too broad to work on; pass the project directory.")
    with standalone_git(directory) as git:
        if git.is_repo():
            return _in_repository(git, directory, run_id, slug, mode, allow_dirty)
        if (directory / ".git").exists():
            raise IsolationError(
                f"{directory} has a .git entry that is not a usable repository "
                "(or git is not installed). Repair it, or install Git."
            )
    if (enclosing := enclosing_repository(directory)) is not None:
        raise IsolationError(
            f"{directory} is inside the Git repository at {enclosing}. Pass the repository's "
            "top level, so the team's branch is made from the whole project."
        )
    if mode != "auto":
        raise IsolationError(
            f"{directory} is not a Git repository, so '{mode}' mode is not possible."
        )
    return _copy(directory, run_id, Path(workspace_root), name, init_git)


# -- a Git repository -----------------------------------------------------------------------


def branch_name(run_id: str, slug: str) -> str:
    return f"{BRANCH_PREFIX}/{run_id}-{_slug(slug)}"


def _slug(text: str) -> str:
    try:
        return slugify_project_name(text)[:MAX_SLUG].strip("-") or "work"
    except ValueError:
        return "work"


def _in_repository(
    git: GitPort, directory: Path, run_id: str, slug: str, mode: Requested, allow_dirty: bool
) -> Isolation:
    head = git.head()
    if head is None:
        raise IsolationError(
            f"The repository at {directory} has no commits yet. Make a first commit, so there is "
            "something to branch from."
        )
    changed, untracked = status_counts(git.status())
    dirty = bool(changed or untracked)
    base_branch = git.current_branch()
    auto: Literal["branch", "worktree"] = "worktree" if dirty and not allow_dirty else "branch"
    chosen = auto if mode == "auto" else mode
    if chosen == "branch" and dirty and not allow_dirty:
        raise IsolationError(
            f"The working tree at {directory} has uncommitted changes ({changed} changed, "
            f"{untracked} untracked). Commit or stash them, use worktree mode (your files stay "
            "exactly as they are and the team works on a copy of the last commit), or pass "
            "--allow-dirty to work in place anyway."
        )
    branch = branch_name(run_id, slug)
    notes: list[str] = []
    try:
        git.exclude_controller_state()
        if chosen == "branch":
            git.create_branch(branch)
            workspace = directory
            if dirty:
                notes.append(
                    "Your uncommitted changes are in the working tree and will be part of the "
                    "team's first commit on the new branch."
                )
        else:
            workspace = directory / WORKTREES / run_id
            git.worktree_add(workspace, branch, "HEAD")
            if dirty:
                notes.append(
                    "Your uncommitted changes are not in the worktree: the team starts from the "
                    f"last commit ({head[:10]})."
                )
    except GitError as exc:
        raise IsolationError(f"Git could not set up the branch '{branch}': {exc}") from exc
    result = Isolation(chosen, directory, workspace, branch, base_branch, head, tuple(notes))
    _record(result)
    return result


# -- a directory that is not a repository --------------------------------------------------


def _copy(
    directory: Path, run_id: str, workspace_root: Path, name: str | None, init_git: bool
) -> Isolation:
    root = workspace_root.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    target = root.resolve() / _slug(name or directory.name)
    if target.exists():
        raise IsolationError(
            f"{target} already exists. Choose another --project-name, or remove it first."
        )
    if target == directory or directory in target.parents or target in directory.parents:
        raise IsolationError(
            f"The copy ({target}) and the project ({directory}) would contain one another; "
            "choose another --workspace-root."
        )
    size = _tree_bytes(directory)
    if size > MAX_COPY_BYTES:
        raise IsolationError(
            f"{directory} is {size / 1024**3:.1f} GiB, too large to copy (limit "
            f"{MAX_COPY_BYTES // 1024**3} GiB). Work from a smaller directory, or put it in Git "
            "so a worktree can be used."
        )
    try:
        shutil.copytree(directory, target, symlinks=True, ignore=_skip_names)
    except (OSError, shutil.Error) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise IsolationError(f"Copying {directory} to {target} failed: {exc}") from exc
    notes = ["Your directory is untouched; the team works on the copy."]
    base: str | None = None
    if init_git:
        try:
            with standalone_git(target) as git:
                git.init()
                base = git.head()
        except GitError as exc:
            raise IsolationError(f"The copy could not be made a Git repository: {exc}") from exc
        notes.append(
            "The copy is a Git repository; its first commit is your directory as imported."
        )
    else:
        notes.append(
            "No Git history: pass --init-git to commit the import as a base to diff against."
        )
    result = Isolation("copy", directory, target, None, None, base, tuple(notes))
    _record(result)
    return result


def _skip_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in COPY_SKIP}


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


# -- the record -----------------------------------------------------------------------------


def _record(isolation: Isolation) -> None:
    """Mark the workspace adopted and note how it was made (isolation.json, in the state dir)."""

    workspace = ProjectWorkspace.create(isolation.workspace)
    adopt_directory(workspace.root, source=isolation.source)
    atomic_write_json(
        workspace.root / RECORD,
        {**isolation.to_json(), "created": datetime.now(UTC).isoformat(timespec="seconds")},
    )


def read_isolation(workspace: Path) -> Isolation | None:
    """The isolation record of a workspace made by :func:`isolate`, or ``None``."""

    try:
        data = json.loads((workspace / RECORD).read_text(encoding="utf-8"))
        return Isolation(
            mode=data["mode"],
            source=Path(data["source"]),
            workspace=Path(data["workspace"]),
            branch=data.get("branch"),
            base_branch=data.get("base_branch"),
            base_commit=data.get("base_commit"),
            notes=tuple(data.get("notes", ())),
        )
    except (OSError, ValueError, KeyError):
        return None
