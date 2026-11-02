"""The body of the draft pull request: the run's verdict, checks, criteria, diff stats and cost.

Everything here is what the controller measured or ran; the agents' own accounts are not
included. Text that comes from the project or the run (check summaries, criteria) is shown as
data: markup is neutralised and ``@`` mentions are broken, so opening the pull request notifies
nobody.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from engineering_team.integrations.issue import IssueRef
from engineering_team.report.render_md import cell, table

MAX_BODY = 60_000
MAX_TITLE = 100
MAX_FILES = 25
MENTION = re.compile(r"@(?=[A-Za-z0-9_])")


@dataclass(frozen=True)
class CheckLine:
    name: str
    status: str
    required: bool
    summary: str = ""


@dataclass(frozen=True)
class CriterionLine:
    id: str
    text: str
    status: str  # verified, referenced or unverified


@dataclass(frozen=True)
class FileLine:
    path: str
    status: str
    added: int | None  # None: a binary file
    removed: int | None


@dataclass(frozen=True)
class PullRequestFacts:
    run_id: str
    verdict: str
    branch: str
    base_branch: str | None = None
    issue: IssueRef | None = None
    closes_issue: bool = False  # the issue is in the repository the pull request opens in
    checks: Sequence[CheckLine] = ()
    criteria: Sequence[CriterionLine] = ()
    files: Sequence[FileLine] = ()
    cost_usd: float | None = None
    duration_seconds: float | None = None
    report_path: str | None = None
    notes: Sequence[str] = field(default=())


def defuse(text: str) -> str:
    """``text`` with its ``@`` mentions broken, so GitHub does not notify anyone."""

    return MENTION.sub("@​", text)


def pull_request_title(title: str, fallback: str) -> str:
    one = " ".join(defuse(title or fallback).split())
    return (one[: MAX_TITLE - 3].rstrip() + "...") if len(one) > MAX_TITLE else one


def render_pr_body(facts: PullRequestFacts) -> str:
    """The Markdown body (within GitHub's size limit)."""

    lines: list[str] = []
    if facts.issue is not None:
        verb = "Closes" if facts.closes_issue else "Refs"
        lines += [f"{verb} {facts.issue.text}", ""]
    where = f"`{facts.branch}`" + (f" → `{facts.base_branch}`" if facts.base_branch else "")
    lines += [
        f"**Verification: {cell(facts.verdict)}** (run `{cell(facts.run_id)}`, branch {where}).",
        "",
        "Opened as a draft by engineering-team. The checks below were run by the controller; "
        "text written by the agents is not evidence. Review the diff before marking it ready.",
    ]
    if facts.checks:
        lines += ["", "### Checks", ""]
        lines += table(
            ["Check", "Result", "Required", "Summary"],
            [
                [c.name, c.status, "yes" if c.required else "no", c.summary[:200]]
                for c in facts.checks
            ],
        )
    if facts.criteria:
        lines += ["", "### Acceptance criteria", ""]
        lines += [
            f"- {cell(c.id)} ({cell(c.status)}): {cell(c.text)[:300]}" for c in facts.criteria
        ]
    lines += ["", "### Changes", "", *_changes(facts.files)]
    lines += ["", "### Cost", "", *_cost(facts)]
    if facts.report_path:
        lines += [
            "",
            "### Full report",
            "",
            f"`{cell(facts.report_path)}` (on the machine that ran the team; it is not uploaded).",
        ]
    lines += [f"\n{cell(note)}" for note in facts.notes]
    body = defuse("\n".join(lines)).rstrip() + "\n"
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - 80].rstrip() + "\n\n_(The rest was cut at GitHub's size limit.)_\n"
    return body


def _changes(files: Sequence[FileLine]) -> list[str]:
    if not files:
        return ["No file changes were measured."]
    added = sum(f.added or 0 for f in files)
    removed = sum(f.removed or 0 for f in files)
    lines = [f"{len(files)} file(s), +{added} −{removed}.", ""]
    lines += table(
        ["File", "Change", "Lines"],
        [
            [f"`{f.path}`", f.status, "binary" if f.added is None else f"+{f.added} −{f.removed}"]
            for f in files[:MAX_FILES]
        ],
    )
    if len(files) > MAX_FILES:
        lines.append(f"\n... and {len(files) - MAX_FILES} more file(s).")
    return lines


def _cost(facts: PullRequestFacts) -> list[str]:
    cost = f"${facts.cost_usd:.4f} estimated" if facts.cost_usd is not None else "cost unknown"
    if facts.duration_seconds is None:
        return [cost + "."]
    minutes, seconds = divmod(int(facts.duration_seconds), 60)
    return [f"{cost}, {minutes}m{seconds:02d}s." if minutes else f"{cost}, {seconds}s."]
