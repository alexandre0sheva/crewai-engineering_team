"""Read-only Git history (log and blame), run through the execution backend.

The commands are fixed argv lists built here, never agent input, so they skip the command
allowlist; the arguments are an integer window and workspace-resolved paths. Pagers, external
diff and fsmonitor commands, and credential prompts are switched off. T19's ``GitPort`` will
replace this module's runner; the parsers stay.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.execution.backend import CommandRecord, CommandSpec
from engineering_team.tools.commands import command_environment

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

DAY_SECONDS = 86_400
MAX_COMMITS = 3000
GIT_SECONDS = 30.0
SAFE_FLAGS = ("--no-pager", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off")
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
    """Log and blame of the run's workspace."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx
        self._checked = False

    def ensure_repository(self) -> None:
        if self._checked:
            return
        if shutil.which("git") is None:
            raise GitUnavailable("git is not installed")
        try:
            self._run("rev-parse", "--is-inside-work-tree")
        except GitUnavailable as exc:
            raise GitUnavailable(
                "the project is not a Git repository, so there is no history to read"
            ) from exc
        self._checked = True

    def commits(self, days: int, max_commits: int = MAX_COMMITS) -> list[Commit]:
        """Non-merge commits of the last ``days`` days (0 = all), newest first."""

        self.ensure_repository()
        args = [
            "log",
            "--no-merges",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--relative",
            "--numstat",
            f"--max-count={max_commits}",
            LOG_FORMAT,
        ]
        if days > 0:
            args.insert(1, f"--since={days}.days.ago")
        try:
            return parse_log(self._run(*args))
        except GitUnavailable as exc:
            raise GitUnavailable(f"git log failed: {exc}") from exc

    def blame(self, path: str, lines: Iterable[int]) -> dict[int, Blame]:
        """Author and time of ``lines`` of ``path``; empty when Git does not track the file."""

        wanted = sorted({line for line in lines if line > 0})
        if not wanted:
            return {}
        self.ensure_repository()
        ranges = [f"-L{line},{line}" for line in wanted]
        try:
            return parse_blame(self._run("blame", "--line-porcelain", *ranges, "--", path))
        except GitUnavailable:
            return {}

    def _run(self, *args: str) -> str:
        workspace = self._ctx.workspace
        spec = CommandSpec(
            argv=("git", *SAFE_FLAGS, *args),
            cwd=workspace.root,
            env={
                **command_environment(workspace),
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            },
            timeout=GIT_SECONDS,
            label="git",
        )
        try:
            record = self._ctx.backend.run(spec)
        except OSError as exc:
            raise GitUnavailable(f"git cannot run ({exc.strerror or exc})") from exc
        if record.cancelled:
            raise GitUnavailable("the run was cancelled")
        if record.timed_out:
            raise GitUnavailable(f"git took longer than {GIT_SECONDS:.0f}s")
        output = _full_output(record)
        if record.exit_code != 0:
            raise GitUnavailable(output.strip().splitlines()[0] if output.strip() else "failed")
        return output


def _full_output(record: CommandRecord) -> str:
    """The complete output; the record only holds a head and tail window of long output."""

    if record.truncated:
        try:
            return record.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return record.output_tail
