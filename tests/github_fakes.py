"""A fake ``gh`` on ``PATH`` and a local bare repository as ``origin``, for the GitHub tests."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = """#!{python}
import json, pathlib, sys

here = pathlib.Path(__file__).resolve().parent
state = json.loads((here / "state.json").read_text())
args = sys.argv[1:]
entry = {{"args": args}}
if "--body-file" in args:
    entry["body"] = pathlib.Path(args[args.index("--body-file") + 1]).read_text()
with (here / "calls.jsonl").open("a") as log:
    log.write(json.dumps(entry) + "\\n")
if args[:2] == ["auth", "status"]:
    if state.get("signed_in", True):
        print("Logged in to github.com")
        sys.exit(0)
    print("You are not logged into any GitHub hosts.")
    sys.exit(1)
if args[:2] == ["issue", "view"]:
    print(json.dumps(state["issue"]))
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    if state.get("pr_error"):
        print(state["pr_error"], file=sys.stderr)
        sys.exit(1)
    print("https://github.com/acme/notes/pull/9")
    sys.exit(0)
print("unexpected gh call", args, file=sys.stderr)
sys.exit(2)
"""

ISSUE = {
    "number": 7,
    "title": "Search should find notes by text",
    "body": "Users cannot find a note again.\n\nAdd a search function over note text.",
    "url": "https://github.com/acme/notes/issues/7",
    "state": "OPEN",
    "author": {"login": "reporter"},
    "labels": [{"name": "enhancement"}],
    "comments": [
        {"author": {"login": "maintainer"}, "body": "Case-insensitive please.", "createdAt": "x"}
    ],
}


class FakeGh:
    """The ``gh`` the tests put first on ``PATH``; ``calls()`` is what it was asked."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def calls(self) -> list[dict[str, Any]]:
        log = self.directory / "calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def verbs(self) -> list[str]:
        return [" ".join(call["args"][:2]) for call in self.calls()]


def install_gh(directory: Path, monkeypatch: pytest.MonkeyPatch, **state: Any) -> FakeGh:
    """Write the fake ``gh`` into ``directory`` and put it first on ``PATH``."""

    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "gh"
    script.write_text(SCRIPT.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (directory / "state.json").write_text(json.dumps({"issue": ISSUE, **state}), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return FakeGh(directory)


def git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    done = subprocess.run(
        ["git", *identity, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout


def add_origin(repo: Path, bare: Path, address: str = "https://github.com/acme/notes.git") -> None:
    """Make ``origin`` a GitHub address that really lives in the bare repository ``bare``."""

    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", address)
    git(repo, "config", f"url.{bare}.insteadOf", address)
