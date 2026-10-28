"""The Git operations that change something: init, checkpoints, branches, worktrees, restore."""

from __future__ import annotations

from pathlib import Path

from engineering_team.git.core import BRANCH, GitError, _clean_ref
from engineering_team.git.reads import GitReads


class GitWrites(GitReads):
    """Operations that change the repository or the working tree (never a remote)."""

    # -- making history ----------------------------------------------------------------

    def init(self) -> bool:
        """Make the project a repository with an initial commit; ``False`` if it already is one.

        The controller's own state and heavy directories (``node_modules``, virtualenvs, build
        output) go into ``.git/info/exclude``, so they are never committed and the project's own
        ``.gitignore`` stays the project's.
        """

        self._require_git()
        if self.is_repo():
            return False
        if (self.workspace.root / ".git").exists():
            raise GitError("The project has a .git entry that is not a usable repository.")
        self._run("init", "--initial-branch=main", "--quiet")
        self._write_exclude()
        self._commit("Initial commit", allow_empty=True)
        self._emit("git.init", branch="main")
        return True

    def checkpoint(self, message: str) -> str:
        """Commit everything in the project (new, changed, and removed files) and return the
        commit's sha. A commit is made even when nothing changed, so every stage has one."""

        self._require_repo()
        sha = self._commit(message, allow_empty=True)
        self._emit("git.checkpoint", sha=sha, message=message[:100])
        return sha

    def create_branch(self, name: str, *, checkout: bool = True) -> None:
        self._require_repo()
        if not BRANCH.fullmatch(name or "") or ".." in name or name.endswith(("/", ".", ".lock")):
            raise GitError(
                f"{name!r} is not a usable branch name: use letters, digits, and . _ / - only."
            )
        if checkout:
            self._run("checkout", "-b", name)
        else:
            self._run("branch", name)

    def worktree_add(self, path: Path, branch: str, base: str = "HEAD") -> None:
        """A new worktree at ``path`` on a new ``branch`` started from ``base``."""

        self._require_repo()
        if not BRANCH.fullmatch(branch or "") or ".." in branch:
            raise GitError(f"{branch!r} is not a usable branch name.")
        self._run("worktree", "add", "-b", branch, str(Path(path)), _clean_ref(base))

    def worktree_remove(self, path: Path) -> None:
        """Remove a worktree that has no uncommitted changes (never forced)."""

        self._require_repo()
        self._run("worktree", "remove", str(Path(path)))
        self._run("worktree", "prune")

    def squash(self, base: str, message: str) -> str:
        """Make everything since ``base`` (which must be an ancestor of HEAD) one commit, with
        the work tree's uncommitted changes included; returns its sha. The branch is rewritten,
        so only call this on a branch the controller made."""

        self._require_repo()
        reference = _clean_ref(base)
        try:
            self._run("merge-base", "--is-ancestor", reference, "HEAD")
        except GitError:
            raise GitError(f"{base} is not an ancestor of the current commit.") from None
        self._run("reset", "--soft", reference)
        sha = self._commit(message, allow_empty=True)
        self._emit("git.squash", sha=sha, base=reference[:12])
        return sha

    def restore(self, ref: str = "HEAD") -> list[str]:
        """Put the work tree back as it was at ``ref``: changed and deleted files are restored,
        files added since are removed. Returns the paths it touched. Nothing is committed, no
        history changes, and ignored files (installed packages, caches) are left alone: this is
        the undo for an attempt that did not work, on a branch the controller made."""

        self._require_repo()
        reference = _clean_ref(ref)
        touched: list[str] = []
        for change in self.changes(reference):
            if change.status == "A":
                target = self.workspace.root / change.path
                target.unlink(missing_ok=True)
            else:
                self._run("checkout", reference, "--", change.path)
            touched.append(change.path)
        if touched:
            self._emit("git.restore", ref=reference[:12], files=len(touched))
        return touched

    def export_patch(self, destination: Path, base: str | None = None) -> Path:
        """Write the changes since ``base`` as a patch (binary files included) that ``git
        apply`` takes on the base commit; returns ``destination``."""

        self._require_repo()
        text = self._work_diff(base, ("--binary", "--full-index"), (), raw=True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8", errors="surrogateescape"))
        return destination
