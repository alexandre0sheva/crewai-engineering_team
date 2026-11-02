"""GitHub through ``gh`` and ``git`` on this machine: read an issue, push a branch, open a draft
pull request.

These are the controller's own steps, never an agent's, and they run on the host (the user's
credentials live there) through the local execution backend, with a fixed argv and no shell. The
controller never reads, stores, passes on or prints a token: ``gh`` and ``git`` find the user's
credentials themselves, and only the environment variables they use for that are handed on.
Nothing here forces a push, and a repository's hooks never run.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engineering_team.execution.backend import CommandSpec
from engineering_team.execution.local import LocalBackend
from engineering_team.integrations.issue import (
    DEFAULT_HOST,
    GithubError,
    Issue,
    IssueRef,
)
from engineering_team.runtime.events import Scrubber
from engineering_team.settings import child_environment, secret_values

GH_SECONDS = 60.0
PUSH_SECONDS = 300.0
MAX_ERROR_CHARS = 400
# What ``gh`` and ``git`` need to find the user's sign-in; nothing else of the environment goes on.
HOST_ENVIRONMENT = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT",
        "SSH_AUTH_SOCK", "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "XDG_CACHE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    }
)  # fmt: skip
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
PULL_URL = re.compile(r"https?://\S+/pull/[0-9]+")
REMOTE_PATTERNS = (
    re.compile(r"(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)"),
    re.compile(r"(?:[^@/]+@)?(?P<host>[^/:]+):(?P<path>[^/].*)"),  # scp style: git@host:owner/repo
)


@dataclass(frozen=True)
class Remote:
    """The repository ``origin`` points at (never with the credentials a URL may carry)."""

    host: str
    repo: str  # owner/name

    @property
    def gh_repo(self) -> str:
        return self.repo if self.host == DEFAULT_HOST else f"{self.host}/{self.repo}"

    @property
    def shown(self) -> str:
        return f"{self.host}/{self.repo}"


def parse_remote(url: str) -> Remote | None:
    """The host and ``owner/name`` of a Git remote URL, or ``None`` if it is not that shape."""

    text = url.strip()
    for pattern in REMOTE_PATTERNS:
        if found := pattern.fullmatch(text):
            parts = found["path"].removesuffix("/").removesuffix(".git").split("/")
            if len(parts) == 2 and all(re.fullmatch(r"[\w.-]+", part) for part in parts):
                return Remote(found["host"].lower(), "/".join(parts))
    return None


class HostRunner:
    """Runs the controller's own ``gh`` and ``git`` commands on this machine."""

    def __init__(self, scratch: Path) -> None:
        self._backend = LocalBackend(scratch / "logs")

    def run(
        self, argv: Sequence[str], cwd: Path, *, timeout: float = GH_SECONDS, check: bool = True
    ) -> tuple[int, str]:
        """``(exit code, output)``; with ``check`` a failure raises ``GithubError``."""

        environment = {k: v for k, v in child_environment().items() if k in HOST_ENVIRONMENT}
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GH_PROMPT_DISABLED"] = "1"
        environment["GH_NO_UPDATE_NOTIFIER"] = "1"
        spec = CommandSpec(
            argv=tuple(argv), cwd=cwd, env=environment, timeout=timeout, label=argv[0], network=True
        )
        try:
            record = self._backend.run(spec)
        except OSError as exc:
            raise GithubError(f"`{argv[0]}` cannot run ({exc.strerror or exc}).") from exc
        text = record.output_tail
        if record.truncated:
            with contextlib.suppress(OSError):
                text = record.log_path.read_text(encoding="utf-8", errors="replace")
        if record.timed_out:
            raise GithubError(f"`{' '.join(argv[:3])}` took longer than {timeout:.0f}s.")
        if record.cancelled:
            raise GithubError("Cancelled.")
        if check and record.exit_code != 0:
            raise GithubError(f"`{' '.join(argv[:3])}` failed: {_error_line(text)}")
        return record.exit_code, text


def _error_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    message = " ".join(lines[-3:]) if lines else "no output"
    scrubber = Scrubber(secret_values())
    return str(scrubber.scrub(message) if scrubber else message)[:MAX_ERROR_CHARS]


@contextlib.contextmanager
def host_runner() -> Iterator[HostRunner]:
    with tempfile.TemporaryDirectory(prefix="engineering-team-github-") as scratch:
        yield HostRunner(Path(scratch))


def require_gh(runner: HostRunner, host: str = DEFAULT_HOST) -> None:
    """``gh`` is installed and signed in to ``host``; the error says how to fix it."""

    if shutil.which("gh") is None:
        raise GithubError(
            "The GitHub command line `gh` is not installed. Install it "
            "(https://cli.github.com) and run `gh auth login`; this tool never handles a token."
        )
    code, _ = runner.run(["gh", "auth", "status", "--hostname", host], Path.cwd(), check=False)
    if code != 0:
        raise GithubError(
            f"`gh` is not signed in to {host}. Run `gh auth login --hostname {host}`."
        )


def _json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:]) if start >= 0 else (None, 0)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise GithubError(f"`gh` did not return the issue as JSON: {_error_line(text)}")
    return data


def fetch_issue(runner: HostRunner, ref: IssueRef) -> Issue:
    """The issue's title, body and comments, read through ``gh`` (read-only)."""

    require_gh(runner, ref.host)
    _, text = runner.run(
        [
            "gh", "issue", "view", str(ref.number), "--repo", ref.gh_repo,
            "--json", "number,title,body,url,state,author,labels,comments",
        ],
        Path.cwd(),
    )  # fmt: skip
    return Issue.from_json(ref, _json_object(text))


def origin_remote(runner: HostRunner, repository: Path) -> Remote:
    """The GitHub repository ``origin`` points at (as configured, before any ``insteadOf``)."""

    code, text = runner.run(
        ["git", "config", "--get", "remote.origin.url"], repository, check=False
    )
    if code != 0 or not text.strip():
        raise GithubError(
            f"{repository} has no remote called 'origin'. Add one (git remote add origin <url>), "
            "or leave out --open-pr and push the branch yourself."
        )
    remote = parse_remote(text.strip().splitlines()[-1])
    if remote is None:
        raise GithubError(
            "The remote 'origin' is not a GitHub repository address "
            "(https://host/owner/repo or git@host:owner/repo)."
        )
    return remote


def remote_has_branch(runner: HostRunner, repository: Path, branch: str) -> bool:
    """Whether ``origin/<branch>`` is known here (a local look; nothing is fetched)."""

    if not BRANCH.fullmatch(branch):
        return False
    code, _ = runner.run(
        ["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"],
        repository,
        check=False,
    )
    return code == 0


def push_branch(runner: HostRunner, workspace: Path, branch: str) -> None:
    """Push ``branch`` to ``origin`` under the same name. Never forced; hooks do not run."""

    if not BRANCH.fullmatch(branch) or ".." in branch:
        raise GithubError(f"{branch!r} is not a branch name that can be pushed.")
    runner.run(
        [
            "git", "-c", "core.hooksPath=/dev/null", "push", "--no-verify", "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ],
        workspace,
        timeout=PUSH_SECONDS,
    )  # fmt: skip


def open_draft_pr(
    runner: HostRunner,
    workspace: Path,
    remote: Remote,
    *,
    head: str,
    base: str | None,
    title: str,
    body_file: Path,
) -> str:
    """Open a draft pull request from ``head`` and return its URL."""

    argv = [
        "gh", "pr", "create", "--draft", "--repo", remote.gh_repo, "--head", head,
        "--title", title, "--body-file", str(body_file),
    ]  # fmt: skip
    if base:
        argv += ["--base", base]
    _, text = runner.run(argv, workspace, timeout=PUSH_SECONDS)
    found = PULL_URL.findall(text)
    if not found:
        raise GithubError(
            f"`gh pr create` did not print the pull request's address: {_error_line(text)}"
        )
    return found[-1]
