"""``GitPort``: the controller's one way to use Git.

Every command is a fixed argv built here (never agent input) and runs through the run's
execution backend, so the Docker sandbox covers it and cancellation and timeouts apply. Hard
rules, enforced by what the port can do rather than by a filter:

* it has no method that pushes, fetches, pulls, touches remotes, or forces anything;
* hooks never run (``core.hooksPath=/dev/null``), nor do commands a repository's own config
  names (``core.fsmonitor``, external diff and text conversion are switched off);
* no pager, no credential helper, no prompt, no system or global configuration;
* it refuses a repository whose top level is not the project itself: a project that sits inside
  some other repository (this tool's own checkout, say) is not a repository of ours;
* the real index is never touched by reads: diffs, patches, and the tree hash use a temporary
  index.

Agent-chosen text (refs, paths, search patterns) is validated before it reaches an argv.
"""

from __future__ import annotations

import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionBackend,
    has_executable,
)
from engineering_team.settings import GitSettings
from engineering_team.tools.commands import command_environment
from engineering_team.tools.workspace import (
    CONTROLLER_DIRECTORY,
    IGNORED_LIST_DIRECTORIES,
    ProjectWorkspace,
)

if TYPE_CHECKING:  # the run context imports this module, and the runtime package imports it
    from engineering_team.runtime.events import EventSink

DEFAULT_NAME = "Engineering Team"
DEFAULT_EMAIL = "engineering-team@users.noreply.github.com"
GIT_SECONDS = 60.0
SCRATCH = (CONTROLLER_DIRECTORY, "tmp", "git")
MAX_OUTPUT_CHARS = 200_000
MAX_LIST = 200

# Config every command runs with (``-c``): no hooks, no fsmonitor, no credential helper, nothing
# that turns repository content into a command.
SAFE_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.quotepath=off",
    "core.autocrlf=false",
    "core.pager=cat",
    "credential.helper=",
    "commit.gpgsign=false",
    "tag.gpgsign=false",
    "gc.auto=0",
    "diff.external=",
    "protocol.allow=never",
)
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/@{}~^:+-]*")
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
SHA = re.compile(r"\b[0-9a-f]{40}(?:[0-9a-f]{24})?\b")


@dataclass(frozen=True)
class Change:
    """One file that differs between a base and the work tree. ``added`` and ``removed`` are
    ``None`` for a binary file."""

    status: str  # A added, M modified, D deleted, T type changed
    path: str
    added: int | None
    removed: int | None

    @property
    def lines(self) -> int:
        """Lines touched (a binary file counts as one)."""

        if self.added is None:
            return 1
        return self.added + (self.removed or 0)


class GitError(Exception):
    """A Git operation that cannot be done; the message says why and how to fix the call."""


def _clean_ref(ref: str) -> str:
    """``ref`` if it is a plain revision (branch, tag, sha, ``HEAD~2``); never a flag."""

    text = ref.strip()
    if not text or not REF.fullmatch(text) or ".." in text or text.endswith("."):
        raise GitError(
            f"{ref!r} is not a usable ref. Pass a branch, tag, commit sha, or HEAD~N "
            "(no spaces, no leading '-', no '..')."
        )
    return text


class GitPort:
    """Git for one project. Thread-safe; see the module docstring for the rules."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        backend: ExecutionBackend,
        settings: GitSettings,
        events: EventSink | None = None,
        *,
        state_dir: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.settings = settings
        self._backend = backend
        self._events = events
        # Where git's home, caches, and temp files go (default: the project's controller
        # directory). A port that only reads, over a project that must stay untouched, points
        # this somewhere else; it must not be used for diffs, which keep a temporary index.
        self._state_dir = state_dir
        self._lock = threading.Lock()  # one index-changing operation at a time
        self._counter = 0
        self._known_repo = False  # a repository stays one for the run; only ``True`` is cached

    # -- what is here ------------------------------------------------------------------

    def available(self) -> bool:
        return has_executable(self._backend, "git")

    def is_repo(self) -> bool:
        """Whether the project directory itself is the top level of a Git repository."""

        if not self.available():
            return False
        if self._known_repo and (self.workspace.root / ".git").exists():
            return True
        try:
            top = self._run("rev-parse", "--show-toplevel").strip().splitlines()[-1]
        except GitError:
            return False
        self._known_repo = Path(top).resolve() == self.workspace.root.resolve()
        return self._known_repo

    def head(self) -> str | None:
        """The commit HEAD points at, or ``None`` before the first commit."""

        try:
            return self._sha(self._run("rev-parse", "--verify", "-q", "HEAD"))
        except GitError:
            return None

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

    def diff_stat(self, base: str | None = None) -> str:
        return self._work_diff(base, ("--stat",), ())

    def export_patch(self, destination: Path, base: str | None = None) -> Path:
        """Write the changes since ``base`` as a patch (binary files included) that ``git
        apply`` takes on the base commit; returns ``destination``."""

        self._require_repo()
        text = self._work_diff(base, ("--binary", "--full-index"), (), raw=True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8", errors="surrogateescape"))
        return destination

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

    # -- internals ---------------------------------------------------------------------

    def _emit(self, type: str, **data: object) -> None:
        if self._events is not None:
            self._events.emit(type, **data)

    def _require_git(self) -> None:
        if not self.available():
            raise GitError("git is not installed. Install Git, or run with --no-git.")

    def _require_repo(self) -> None:
        self._require_git()
        if self.is_repo():
            return
        if (self.workspace.root / ".git").exists():
            raise GitError("The project's .git is not a usable repository.")
        raise GitError("The project is not a Git repository.")

    def _paths(self, path: str) -> list[str]:
        """``-- <path>`` for a project path, validated like any agent-supplied path."""

        if not path.strip():
            return []
        resolved = self.workspace.resolve(path)  # raises for .git, .engineering-team, outside
        return ["--", self.workspace.relative_name(resolved)]

    def _bounded(self, text: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        return text[:MAX_OUTPUT_CHARS] + f"\n... truncated at {MAX_OUTPUT_CHARS} characters."

    @staticmethod
    def _sha(output: str) -> str:
        found = SHA.findall(output)
        if not found:
            raise GitError(
                f"Git did not return a commit or tree id (got: {output.strip()[:200]!r})."
            )
        return found[-1]

    def _identity(self) -> dict[str, str]:
        name = self.settings.author_name or DEFAULT_NAME
        email = self.settings.author_email or DEFAULT_EMAIL
        return {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }

    def _commit(self, message: str, *, allow_empty: bool) -> str:
        with self._lock:
            self._write_exclude()  # whatever repository this is, the controller's state stays out
            self._run("add", "--all", "--", ".")
            args = ["commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", message]
            if allow_empty:
                args.append("--allow-empty")
            self._run(*args, env=self._identity())
        sha = self.head()
        if sha is None:
            raise GitError("The commit was not created.")
        return sha

    def exclude_controller_state(self) -> None:
        """Keep the controller's state and heavy directories out of the repository's view
        (``.git/info/exclude``; the project's own ``.gitignore`` is never edited)."""

        self._require_repo()
        self._write_exclude()

    def git_dir(self) -> Path:
        """This working tree's own git directory: ``.git``, or for a linked worktree the
        directory its ``.git`` file points to."""

        dot_git = self.workspace.root / ".git"
        if dot_git.is_file():
            for line in dot_git.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line.partition(":")[2].strip())
                    return target if target.is_absolute() else (dot_git.parent / target).resolve()
        return dot_git

    def common_dir(self) -> Path:
        """The git directory every linked worktree of the repository shares (``info/exclude``,
        refs and objects live there)."""

        git_dir = self.git_dir()
        pointer = git_dir / "commondir"
        if pointer.is_file():
            return (git_dir / pointer.read_text(encoding="utf-8").strip()).resolve()
        return git_dir

    def _write_exclude(self) -> None:
        exclude = self.common_dir() / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"{CONTROLLER_DIRECTORY}/",
            *sorted(f"{name}/" for name in IGNORED_LIST_DIRECTORIES),
        ]
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        wanted = [line for line in lines if line not in existing.splitlines()]
        if wanted:
            header = "# Added by the engineering team: controller state and heavy directories\n"
            exclude.write_text(existing + header + "\n".join(wanted) + "\n", encoding="utf-8")

    def _work_diff(
        self, base: str | None, flags: tuple[str, ...], paths: tuple[str, ...], *, raw: bool = False
    ) -> str:
        self._require_repo()
        limit = ["--", *[self.workspace.relative_name(self.workspace.resolve(p)) for p in paths]]
        reference = _clean_ref(base) if base else (self.head() or self._empty_tree())
        with self._temporary_index() as env:
            text = self._run(
                "diff", "--cached", "--no-ext-diff", "--no-textconv", *flags, reference,
                *(limit if paths else []), env=env, bounded=not raw,
            )  # fmt: skip
        return text if raw else self._bounded(text)

    def _empty_tree(self) -> str:
        return self._sha(self._run("hash-object", "-t", "tree", "--stdin"))

    def _temporary_index(self) -> _TempIndex:
        with self._lock:
            self._counter += 1
            name = f"index-{self._counter}"
        return _TempIndex(self, self.workspace.root.joinpath(*SCRATCH) / name)

    def _run(
        self,
        *args: str,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        optional_locks: bool = True,
        bounded: bool = True,
    ) -> str:
        self._require_git()
        root = self.workspace.root
        config = [item for entry in SAFE_CONFIG for item in ("-c", entry)]
        spec = CommandSpec(
            argv=("git", "--no-pager", *config, *args),
            cwd=root,
            env={
                **command_environment(self.workspace, self._state_dir),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_PAGER": "cat",
                "GIT_CEILING_DIRECTORIES": str(root.parent),
                "GIT_ASKPASS": "true",
                "GIT_OPTIONAL_LOCKS": "1" if optional_locks else "0",
                **(env or {}),
            },
            timeout=GIT_SECONDS,
            label="git",
        )
        try:
            record = self._backend.run(spec)
        except OSError as exc:
            raise GitError(f"git cannot run ({exc.strerror or exc})") from exc
        if record.cancelled:
            raise GitError("the run was cancelled")
        if record.timed_out:
            raise GitError(f"git took longer than {GIT_SECONDS:.0f}s")
        text = _full_output(record)
        if record.exit_code != 0 and check:
            lines = text.strip().splitlines()
            raise GitError(lines[-1] if lines else f"git {args[0]} failed")
        return text


class _TempIndex:
    """A throwaway index holding the work tree, so reads never stage anything for real."""

    def __init__(self, port: GitPort, path: Path) -> None:
        self._port = port
        self._path = path

    def __enter__(self) -> dict[str, str]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        real = self._port.git_dir() / "index"
        if real.is_file():
            shutil.copy2(
                real, self._path
            )  # a warm stat cache makes `add` fast; keep its mtime (racy git)
        self._port._write_exclude()
        env = {"GIT_INDEX_FILE": str(self._path)}
        self._port._run("add", "--all", "--", ".", env=env)
        return env

    def __exit__(self, *_exc: object) -> None:
        self._path.unlink(missing_ok=True)
        self._path.with_suffix(".lock").unlink(missing_ok=True)


def _count(value: int) -> int:
    return max(1, min(int(value), MAX_LIST))


def _text(value: str, what: str) -> str:
    if not value.strip() or "\n" in value or "\x00" in value:
        raise GitError(f"The {what} must be one non-empty line.")
    return value


def _full_output(record: CommandRecord) -> str:
    """The complete output; the record only holds a head and tail window of long output."""

    if record.truncated:
        try:
            return record.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return record.output_tail
