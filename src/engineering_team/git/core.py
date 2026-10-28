"""The plumbing every Git operation shares: the safe command line, the index, and the state."""

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


class GitCore:
    """Git for one project, minus the operations. Thread-safe; see ``port`` for the rules."""

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

    def head(self) -> str | None:
        """The commit HEAD points at, or ``None`` before the first commit."""

        try:
            return self._sha(self._run("rev-parse", "--verify", "-q", "HEAD"))
        except GitError:
            return None

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

    def __init__(self, port: GitCore, path: Path) -> None:
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
