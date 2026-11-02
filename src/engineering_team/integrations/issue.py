"""A GitHub issue as a request: parsing ``owner/repo#N`` and issue URLs, and turning the issue
(title, body, comments) into the request text.

Issue text is written by other people. It describes the work and is the request, but it carries
no authority: the same rules apply as to any request (the controller verifies the result, and
nothing in the text changes permissions, budgets or checks).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json

DEFAULT_HOST = "github.com"
ISSUE_FILE = "github_issue.json"  # the run's record of the issue it was started from
NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
SHORT = re.compile(rf"(?P<repo>{NAME}/{NAME})#(?P<number>[1-9][0-9]{{0,8}})")
URL = re.compile(
    rf"https?://(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*)/(?P<repo>{NAME}/{NAME})"
    r"/issues/(?P<number>[1-9][0-9]{0,8})/?(?:[?#]\S*)?"
)
MAX_TITLE = 200
OMITTED = "[... omitted: the issue is longer than the request limit ...]"


class GithubError(ValueError):
    """A GitHub step that cannot be done (a usage error); the message says how to fix it."""


@dataclass(frozen=True)
class IssueRef:
    repo: str  # owner/name
    number: int
    host: str = DEFAULT_HOST

    @property
    def text(self) -> str:
        """``owner/repo#N`` (``host/owner/repo#N`` away from github.com)."""

        prefix = "" if self.host == DEFAULT_HOST else f"{self.host}/"
        return f"{prefix}{self.repo}#{self.number}"

    @property
    def gh_repo(self) -> str:
        """The value of ``gh --repo`` for this issue's repository."""

        return self.repo if self.host == DEFAULT_HOST else f"{self.host}/{self.repo}"


def parse_issue_ref(text: str) -> IssueRef:
    """``owner/repo#N`` or an issue URL; ``GithubError`` says what is accepted otherwise."""

    value = text.strip()
    if found := SHORT.fullmatch(value):
        return IssueRef(found["repo"], int(found["number"]))
    if found := URL.fullmatch(value):
        return IssueRef(found["repo"], int(found["number"]), found["host"].lower())
    raise GithubError(
        f"{text!r} is not a GitHub issue. Use owner/repo#123 or an issue URL "
        "(https://github.com/owner/repo/issues/123)."
    )


@dataclass(frozen=True)
class Comment:
    author: str
    body: str


@dataclass(frozen=True)
class Issue:
    ref: IssueRef
    title: str
    body: str
    url: str = ""
    state: str = ""
    author: str = ""
    labels: tuple[str, ...] = ()
    comments: tuple[Comment, ...] = field(default=())

    @classmethod
    def from_json(cls, ref: IssueRef, data: dict[str, Any]) -> Issue:
        def login(entry: Any) -> str:
            return str((entry or {}).get("login") or "") if isinstance(entry, dict) else ""

        return cls(
            ref=ref,
            title=" ".join(str(data.get("title") or "").split())[:MAX_TITLE],
            body=str(data.get("body") or "").replace("\r\n", "\n").strip(),
            url=str(data.get("url") or ""),
            state=str(data.get("state") or "").lower(),
            author=login(data.get("author")),
            labels=tuple(
                str(item.get("name"))
                for item in data.get("labels") or []
                if isinstance(item, dict) and item.get("name")
            ),
            comments=tuple(
                Comment(login(item.get("author")) or "unknown", str(item.get("body") or "").strip())
                for item in data.get("comments") or []
                if isinstance(item, dict) and str(item.get("body") or "").strip()
            ),
        )

    def request_text(self, limit: int) -> str:
        """The request this issue makes, within ``limit`` characters: the title and body, then as
        many comments as fit, newest first, shown oldest first."""

        facts = [f"Issue {self.ref.text}"]
        facts += [self.state] if self.state else []
        facts += [f"opened by {self.author}"] if self.author else []
        facts += [f"labels: {', '.join(self.labels)}"] if self.labels else []
        head = f"# {self.title or '(no title)'}\n\n{' · '.join(facts)}"
        head += f"\n{self.url}" if self.url else ""
        body = self.body or "(the issue has no description)"
        budget = max(limit, 1_000)
        room = budget - len(head) - 40
        if len(body) > room * 2 // 3:
            body = body[: room * 2 // 3].rstrip() + f"\n\n{OMITTED}"
        text = f"{head}\n\n{body}"
        left = budget - len(text) - 120
        shown: list[str] = []
        for comment in reversed(self.comments):
            block = f"### {comment.author} commented\n\n{comment.body}"
            if len(block) > left:
                block = block[: max(left, 0)].rstrip()
                if len(block) > 60:
                    shown.append(block + f"\n\n{OMITTED}")
                break
            shown.append(block)
            left -= len(block) + 2
        if shown:
            skipped = len(self.comments) - len(shown)
            note = f" ({skipped} older not shown)" if skipped > 0 else ""
            text += f"\n\n## Comments{note}\n\n" + "\n\n".join(reversed(shown))
        return text


@dataclass(frozen=True)
class IssueCite:
    """What the report says about the issue a run was started from."""

    ref: str
    title: str
    url: str

    @property
    def line(self) -> str:
        return " ".join(part for part in (self.ref, self.title, f"({self.url})" if self.url else "")
                        if part)  # fmt: skip


def save_issue(run_dir: Path, issue: Issue) -> None:
    atomic_write_json(
        run_dir / ISSUE_FILE,
        {"ref": issue.ref.text, "title": issue.title, "url": issue.url, "state": issue.state},
    )


def load_cite(run_dir: Path) -> IssueCite | None:
    """The issue the run was started from, if it was (a missing or damaged record is ``None``)."""

    try:
        data = json.loads((run_dir / ISSUE_FILE).read_text(encoding="utf-8"))
        return IssueCite(str(data["ref"]), str(data.get("title") or ""), str(data.get("url") or ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None
