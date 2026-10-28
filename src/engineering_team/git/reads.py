"""The read-only Git operations: status, diffs, history, blame, search."""

from __future__ import annotations

from engineering_team.git.core import Change, GitCore, GitError, _clean_ref, _count, _text


class GitReads(GitCore):
    """Operations that only look: the real index and history are never changed."""

    def current_branch(self) -> str:
        self._require_repo()
        text = self._run("symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
        return text.splitlines()[-1] if text else "HEAD (detached)"

    def status(self) -> str:
        """Porcelain status lines (``XY path``), controller state excluded."""

        self._require_repo()
        return self._run("status", "--porcelain", "--untracked-files=all", optional_locks=False)

    def is_dirty(self) -> bool:
        return bool(self.status().strip())

    # -- the work as it is now (never touches the real index) --------------------------

    def tree_hash(self) -> str:
        """The Git tree object hash of the project as it is now, untracked files included."""

        self._require_repo()
        with self._temporary_index() as env:
            return self._sha(self._run("write-tree", env=env))

    def diff(self, base: str | None = None, paths: tuple[str, ...] = ()) -> str:
        """Unified diff from ``base`` (default HEAD) to the work tree, new files included."""

        return self._work_diff(base, ("--no-color",), paths)

    def changes(self, base: str | None = None) -> list[Change]:
        """The files that differ between ``base`` (default HEAD) and the work tree, new files
        included, with the lines each touched; sorted by path."""

        text = self._work_diff(base, ("--raw", "--numstat", "--no-renames"), (), raw=True)
        status: dict[str, str] = {}
        counts: dict[str, tuple[int | None, int | None]] = {}
        for line in text.splitlines():
            if line.startswith(":"):
                head, _, path = line.partition("\t")
                status[path] = head.split()[-1][:1]
            elif line.count("\t") == 2:
                added, removed, path = line.split("\t")
                counts[path] = (
                    (int(added), int(removed))
                    if added.isdigit() and removed.isdigit()
                    else (None, None)
                )
        return [
            Change(status.get(path, "M"), path, *counts.get(path, (0, 0)))
            for path in sorted(set(status) | set(counts))
        ]

    def merge_base(self, first: str, second: str = "HEAD") -> str:
        """The commit where ``first`` and ``second`` last agreed (where a branch left its base)."""

        self._require_repo()
        return self._sha(self._run("merge-base", _clean_ref(first), _clean_ref(second)))

    def resolves(self, ref: str) -> bool:
        """Whether ``ref`` names a commit here."""

        self._require_repo()
        try:
            self._run("rev-parse", "--verify", "-q", f"{_clean_ref(ref)}^{{commit}}")
        except GitError:
            return False
        return True

    def diff_stat(self, base: str | None = None) -> str:
        return self._work_diff(base, ("--stat",), ())

    # -- reading history (the agent tools call these) ----------------------------------

    def log(
        self, *, max_count: int = 20, path: str = "", ref: str = "HEAD", author: str = ""
    ) -> str:
        self._require_repo()
        args = ["log", f"--max-count={_count(max_count)}", "--format=%h %ad %an  %s"]
        args += ["--date=short", "--no-ext-diff", _clean_ref(ref)]
        if author.strip():
            args.insert(-1, f"--author={_text(author, 'author')}")
        return self._bounded(self._run(*args, *self._paths(path)))

    def show(self, ref: str, path: str = "", *, stat: bool = False) -> str:
        self._require_repo()
        args = ["show", "--no-ext-diff", "--no-textconv", "--format=fuller"]
        if stat:
            args.append("--stat")
        return self._bounded(self._run(*args, _clean_ref(ref), *self._paths(path)))

    def blame(self, path: str, *, start: int = 0, end: int = 0) -> str:
        self._require_repo()
        if not path.strip():
            raise GitError("blame needs a file path.")
        args = ["blame", "--date=short"]
        if start > 0:
            args.append(f"-L{start},{max(end, start)}")
        return self._bounded(self._run(*args, *self._paths(path)))

    def search(
        self, pattern: str, *, mode: str = "string", path: str = "", max_count: int = 20
    ) -> str:
        """Commits that added or removed ``pattern`` (``string``: ``log -S``, ``regex``: ``-G``)."""

        self._require_repo()
        flag = {"string": "-S", "regex": "-G"}.get(mode)
        if flag is None:
            raise GitError("mode must be 'string' or 'regex'.")
        args = ["log", f"--max-count={_count(max_count)}", "--format=%h %ad %an  %s"]
        args += [
            "--date=short",
            "--no-ext-diff",
            "--no-textconv",
            f"{flag}{_text(pattern, 'pattern')}",
        ]
        return self._bounded(self._run(*args, *self._paths(path)))

    def numstat_log(self, days: int, max_count: int, pretty: str) -> str:
        """``git log --numstat`` of non-merge commits (the last ``days`` days; 0 = all), shown in
        the caller's ``--format`` text. For code intelligence's churn and ownership figures."""

        self._require_repo()
        args = ["log", "--no-merges", "--no-renames", "--no-ext-diff", "--no-textconv"]
        args += ["--relative", "--numstat", f"--max-count={int(max_count)}", pretty]
        if days > 0:
            args.insert(1, f"--since={int(days)}.days.ago")
        return self._run(*args, optional_locks=False)

    def blame_porcelain(self, path: str, lines: list[int]) -> str:
        """``git blame --line-porcelain`` for single ``lines`` of one project file."""

        self._require_repo()
        ranges = [f"-L{int(line)},{int(line)}" for line in lines]
        return self._run(
            "blame", "--line-porcelain", *ranges, *self._paths(path), optional_locks=False
        )

    def diff_refs(self, first: str, second: str, *, path: str = "", stat: bool = False) -> str:
        """The change from ``first`` to ``second`` (two commits, not the work tree)."""

        self._require_repo()
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
        if stat:
            args.append("--stat")
        refs = [_clean_ref(first), _clean_ref(second)]
        return self._bounded(self._run(*args, *refs, *self._paths(path)))
