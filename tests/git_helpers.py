"""Build real Git repositories with a synthetic history for the code intelligence tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

DAY = 86_400


def require_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")


def git(root: Path, *args: str, author: str = "Test", days_ago: float = 0) -> None:
    stamp = f"{int(time.time() - days_ago * DAY)} +0000"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )


def init_repo(root: Path) -> None:
    require_git()
    git(root, "init", "-q")


def commit(root: Path, files: dict[str, str], message: str, author: str, days_ago: float) -> None:
    """Write ``files`` (relative path -> content), stage everything, and commit as ``author``."""

    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(root, "add", "-A", ":!.engineering-team")
    git(root, "commit", "-q", "-m", message, author=author, days_ago=days_ago)
