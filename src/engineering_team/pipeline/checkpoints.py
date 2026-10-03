"""Git history for a pipeline run: a repository for a new project, a commit per finished stage.

Best effort by design: the history is a convenience (free rollback, a readable log), so a Git
problem (not installed, a stale lock, a read-only directory) is reported as a ``git.warning``
event and never fails the run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from engineering_team.git.port import GitError
from engineering_team.runtime.context import RunContext
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

MAX_SUBJECT = 72


class Checkpoints:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.git = ctx.git

    @property
    def enabled(self) -> bool:
        return self.ctx.settings.git.enabled

    @staticmethod
    def is_new_project(root: Path) -> bool:
        """Whether ``root`` holds nothing of the project's yet (only the controller's state)."""

        try:
            return not any(
                entry.name not in (CONTROLLER_DIRECTORY, ".git") for entry in root.iterdir()
            )
        except FileNotFoundError:
            return True

    def start(self) -> None:
        """Make a new project a repository (with an initial commit). A project that is already
        a repository is continued as it is; one that has files but no repository is left alone."""

        if not self.enabled:
            return
        self._guard(self._start)

    def stage(self, name: str, summary: str) -> None:
        """Commit what the finished stage ``name`` left in the project."""

        if self.enabled:
            self._guard(lambda: self._commit(f"stage({name}): {_subject(summary)}"))

    def final(self, squash_message: str = "") -> None:
        """After the last stage: commit what the final verification changed, if anything, and,
        with ``git.squash``, make the team's commits since it started one commit."""

        if self.enabled:
            self._guard(self._final)
            if self.ctx.settings.git.squash and squash_message:
                self._guard(lambda: self._squash(squash_message))

    # -- internals ---------------------------------------------------------------------

    def _start(self) -> None:
        if self.git.is_repo() or not self.is_new_project(self.ctx.workspace.root):
            return
        self.git.init()

    def _commit(self, message: str) -> None:
        if self.git.is_repo():
            self.git.checkpoint(message)

    def _final(self) -> None:
        if self.git.is_repo() and self.git.is_dirty():
            self.git.checkpoint("final: the project changed after the last stage (final check)")

    def _squash(self, message: str) -> None:
        from engineering_team.modes.isolation import read_isolation

        isolation = read_isolation(self.ctx.workspace.root)
        if isolation is None or isolation.base_commit is None or not self.git.is_repo():
            return  # not a repository mode, or no commit to squash back to
        self.git.squash(isolation.base_commit, _subject(message))

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except (GitError, OSError) as exc:
            self.ctx.events.emit("git.warning", error=str(exc)[:300])


def _subject(summary: str) -> str:
    line = next((row.strip() for row in summary.splitlines() if row.strip()), "completed")
    return line if len(line) <= MAX_SUBJECT else line[: MAX_SUBJECT - 1] + "…"
