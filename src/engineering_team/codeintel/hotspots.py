"""Hotspots: files that change often *and* are big or branchy, the likeliest places for bugs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.codeintel.git import GitHistory
from engineering_team.tools.ignore import IgnoreRules, read_text_or_none

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

# Churn that says nothing about the code: generated, vendored, or lock files.
NOISE = re.compile(
    r"(?:^|/)(?:package-lock\.json|pnpm-lock\.yaml|yarn\.lock|uv\.lock|poetry\.lock|Cargo\.lock"
    r"|go\.sum|composer\.lock|Gemfile\.lock)$|\.(?:lock|min\.js|min\.css|map|snap|svg)$"
)
BRANCH = re.compile(
    r"\b(?:if|elif|else if|elsif|for|foreach|while|until|case|when|catch|except|rescue|match)\b"
)


@dataclass(frozen=True)
class Hotspot:
    path: str
    commits: int
    added: int
    deleted: int
    authors: int
    lines: int
    complexity: int
    score: int


@dataclass(frozen=True)
class HotspotReport:
    spots: list[Hotspot]
    commits: int  # commits scanned
    files: int  # eligible files changed in the window
    days: int


def _within(path: str, prefix: str) -> bool:
    prefix = prefix.strip("/")
    return prefix in ("", ".") or path == prefix or path.startswith(prefix + "/")


def compute_hotspots(
    ctx: RunContext, *, days: int = 365, top: int = 10, path_prefix: str = ""
) -> HotspotReport:
    """Rank files changed in the last ``days`` days (0 = all history) by churn x complexity.

    Complexity is a cheap proxy: decision points (``if``, ``for``, ``while``, ``case``,
    ``catch``, ...) plus one per 20 lines, never below 1. Raises ``GitUnavailable`` without history.
    """

    commits = GitHistory(ctx).commits(days)
    stats: dict[str, tuple[int, int, int, set[str]]] = {}
    for commit in commits:
        for change in commit.files:
            if NOISE.search(change.path) or not _within(change.path, path_prefix):
                continue
            count, added, deleted, authors = stats.get(change.path, (0, 0, 0, set()))
            authors.add(commit.author)
            stats[change.path] = (
                count + 1,
                added + change.added,
                deleted + change.deleted,
                authors,
            )

    workspace = ctx.workspace
    rules = IgnoreRules.for_workspace(workspace)
    spots: list[Hotspot] = []
    for path, (count, added, deleted, authors) in stats.items():
        target = workspace.root / path
        if rules.ignores(path, is_dir=False) or target.is_symlink() or not target.is_file():
            continue  # deleted since, or not ours to read
        text = read_text_or_none(target)
        if text is None:
            continue
        lines = len(text.splitlines())
        complexity = max(1, len(BRANCH.findall(text)) + lines // 20)
        spots.append(
            Hotspot(
                path, count, added, deleted, len(authors), lines, complexity, count * complexity
            )
        )
    spots.sort(key=lambda spot: (-spot.score, -spot.commits, spot.path))
    return HotspotReport(spots[: max(1, min(top, 50))], len(commits), len(spots), days)


def format_hotspots(report: HotspotReport) -> str:
    window = f"the last {report.days} days" if report.days > 0 else "the whole history"
    if not report.spots:
        return f"No hotspots: no eligible files changed in {window} ({report.commits} commits)."
    lines = [
        f"Hotspots: top {len(report.spots)} of {report.files} changed file(s) in {window} "
        f"({report.commits} commits), riskiest first"
    ]
    for rank, spot in enumerate(report.spots, start=1):
        lines.append(
            f"{rank}. {spot.path}  score {spot.score}  ({spot.commits} commits, "
            f"+{spot.added}/-{spot.deleted}, {spot.authors} author(s), {spot.lines} lines, "
            f"complexity {spot.complexity})"
        )
    lines.append(
        "score = commits x complexity; complexity = decision points + lines/20 (a rough proxy). "
        "Lock files, minified and binary files are left out. Read the top files before "
        "changing them."
    )
    return "\n".join(lines)
