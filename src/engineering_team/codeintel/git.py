"""Read-only Git history (log and blame) for code intelligence: parsers over ``GitPort`` output.

The port builds the commands (fixed argv through the execution backend, no pager, no hooks, and
only the project's own repository); this module reads what they print.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.git.port import GitError

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

DAY_SECONDS = 86_400
MAX_COMMITS = 3000
LOG_FORMAT = "--format=%x1e%H%x1f%aN%x1f%ct"


class GitUnavailable(Exception):
    """There is no usable Git history; the message says why, for the agent to read."""


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    deleted: int


@dataclass(frozen=True)
class Commit:
    author: str
    when: int  # Unix seconds
    files: tuple[FileChange, ...]


@dataclass(frozen=True)
class Blame:
    author: str
    when: int | None  # None for lines that are not committed yet


def parse_log(text: str) -> list[Commit]:
    """Commits from ``git log --numstat`` with :data:`LOG_FORMAT` (binary files count as 0/0)."""

    commits: list[Commit] = []
    for chunk in text.split("\x1e")[1:]:
        header, _, body = chunk.partition("\n")
        fields = header.split("\x1f")
        if len(fields) != 3 or not fields[2].strip().isdigit():
            continue
        files: list[FileChange] = []
        for line in body.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                added, deleted, path = parts
                files.append(
                    FileChange(
                        path,
                        int(added) if added.isdigit() else 0,
                        int(deleted) if deleted.isdigit() else 0,
                    )
                )
        commits.append(Commit(fields[1], int(fields[2]), tuple(files)))
    return commits


def parse_blame(text: str) -> dict[int, Blame]:
    """Author and time per final line number from ``git blame --line-porcelain``."""

    result: dict[int, Blame] = {}
    final_line = 0
    author = ""
    when: int | None = None
    for line in text.splitlines():
        if line.startswith("\t"):
            if final_line:
                if author == "Not Committed Yet":  # Git reports a time for it; it means nothing
                    result[final_line] = Blame("uncommitted", None)
                else:
                    result[final_line] = Blame(author, when)
            final_line, author, when = 0, "", None
        elif line.startswith("author "):
            author = line[len("author ") :]
        elif line.startswith("author-time "):
            value = line.split()[1]
            when = int(value) if value.isdigit() else None
        elif not final_line:
            fields = line.split()
            if len(fields) >= 3 and len(fields[0]) >= 40 and fields[2].isdigit():
                final_line = int(fields[2])
    return result


class GitHistory:
    """Log and blame of the run's workspace, read through the run's ``GitPort``."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def ensure_repository(self) -> None:
        git = self._ctx.git
        if not git.available():
            raise GitUnavailable("git is not installed")
        if not git.is_repo():
            raise GitUnavailable(
                "the project is not a Git repository, so there is no history to read"
            )

    def commits(self, days: int, max_commits: int = MAX_COMMITS) -> list[Commit]:
        """Non-merge commits of the last ``days`` days (0 = all), newest first."""

        self.ensure_repository()
        try:
            return parse_log(self._ctx.git.numstat_log(days, max_commits, LOG_FORMAT))
        except GitError as exc:
            raise GitUnavailable(f"git log failed: {exc}") from exc

    def blame(self, path: str, lines: Iterable[int]) -> dict[int, Blame]:
        """Author and time of ``lines`` of ``path``; empty when Git does not track the file."""

        wanted = sorted({line for line in lines if line > 0})
        if not wanted:
            return {}
        self.ensure_repository()
        try:
            return parse_blame(self._ctx.git.blame_porcelain(path, wanted))
        except (GitError, ValueError):
            return {}
