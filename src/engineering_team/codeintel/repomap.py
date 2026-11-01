"""The ranked repo map: the files that matter for a request, with their key definitions, within a
token budget (``Repo Map`` with ``for_request``, and the map excerpt of a stage's context pack).

:func:`build_ranking` does the work once (index, import graph, seeds, ranking); :func:`render_map`
then cuts as many files as fit a budget, so the same ranking serves a stage with a large pack and
one with a small pack.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.codeintel.git import Commit, GitHistory, GitUnavailable
from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.rank import Ranked, rank_files, recency_weights
from engineering_team.codeintel.seeds import Seeds, seeds_from
from engineering_team.tools.workspace import ProjectWorkspace

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

CHARS_PER_TOKEN = 4
RECENCY_DAYS = 90
FULL_FILES = 3  # the best files list this many definitions...
NEAR_FILES = 10  # ...the next ones this many...
FULL_SYMBOLS, NEAR_SYMBOLS, FAR_SYMBOLS = 8, 5, 3  # ...and the rest this many
MIN_SCORE = 0.02  # files scoring below this share of the best are noise, whatever the budget


@dataclass
class RankedFiles:
    """The ranking of one repository for one request."""

    index: SourceIndex
    ranked: list[Ranked]
    seeds: Seeds


@dataclass(frozen=True)
class MapExcerpt:
    """The text of a map and what it shows."""

    text: str
    files: tuple[str, ...]  # the files shown, best first
    candidates: int  # the files that could have been shown

    @property
    def tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN


def build_ranking(
    workspace: ProjectWorkspace,
    request: str,
    *,
    focus_paths: Iterable[str] = (),
    files: Iterable[str] = (),
    commits: Iterable[Commit] = (),
    recency_days: int = RECENCY_DAYS,
) -> RankedFiles:
    """Rank the workspace's source files for ``request`` (an empty one ranks by importance)."""

    index = SourceIndex.build(workspace)
    seeds = seeds_from(index, request, focus_paths=focus_paths, files=files)
    recent = recency_weights(commits, time.time(), recency_days)
    return RankedFiles(index, rank_files(index, ImportGraph(index), seeds, recent), seeds)


def _symbols(found: RankedFiles, path: str, limit: int) -> list[str]:
    """The definitions of ``path`` to list: ones the request names first, then the top-level
    ones in file order (methods only for a class that is listed)."""

    named = found.seeds.symbols
    definitions = [d for d in found.index.definitions(path) if not d.name.startswith("__")]
    ordered = sorted(
        definitions,
        key=lambda d: (d.name not in named, d.parent is not None, d.line),
    )[:limit]
    return [f"  {d.kind} {d.qualified}  L{d.line}" for d in sorted(ordered, key=lambda d: d.line)]


def render_map(
    found: RankedFiles, budget_tokens: int, *, prefix: str = "", header: str = ""
) -> MapExcerpt:
    """The best files of ``found`` below ``prefix`` that fit ``budget_tokens``, one block each:
    the path, why it ranks, and its definitions. At least one file is always shown."""

    budget = max(1, budget_tokens) * CHARS_PER_TOKEN
    scoped = [
        item
        for item in found.ranked
        if not prefix or item.path == prefix or item.path.startswith(prefix.rstrip("/") + "/")
    ]
    scoped = [item for item in scoped if item.score >= MIN_SCORE]
    lines = [header] if header else []
    used = len(header) + 1 if header else 0
    shown: list[str] = []
    for position, item in enumerate(scoped):
        limit = (
            FULL_SYMBOLS
            if position < FULL_FILES
            else NEAR_SYMBOLS
            if position < NEAR_FILES
            else FAR_SYMBOLS
        )
        symbols = _symbols(found, item.path, limit)
        note = f"  ({'; '.join(item.why)})" if item.why and position < NEAR_FILES else ""
        block = "\n".join([f"{item.path}{note}", *symbols])
        if shown and used + len(block) + 1 > budget:
            break
        lines.append(block)
        used += len(block) + 1
        shown.append(item.path)
    return MapExcerpt("\n".join(lines) if shown else "", tuple(shown), len(scoped))


def recent_commits(ctx: RunContext) -> list[Commit]:
    """The commits that count for recency (``context.recency_days``); none without a Git history."""

    days = ctx.settings.context.recency_days
    if days <= 0:
        return []
    try:
        return GitHistory(ctx).commits(days)
    except GitUnavailable:
        return []


def ranked_repo_map(ctx: RunContext, request: str, path: str = ".", size: int = 1500) -> str:
    """``Repo Map`` for a request: the files under ``path`` ranked for it, in ``size`` tokens."""

    workspace = ctx.workspace
    base = workspace.resolve(path, must_exist=True)
    prefix = "" if base == workspace.root else workspace.relative_name(base)
    found = build_ranking(
        workspace,
        request,
        commits=recent_commits(ctx),
        recency_days=ctx.settings.context.recency_days,
    )
    budget = max(400, min(int(size), 12_000))
    header = f"Repo map for {path}, ranked for the request (best match first)"
    excerpt = render_map(found, budget, prefix=prefix, header=header)
    if not excerpt.files:
        return (
            f"No supported source files under {path}. Use Project Tree to see what exists, or "
            "call Repo Map without for_request."
        )
    more = excerpt.candidates - len(excerpt.files)
    tail = (
        f"\n... {more} more file(s) not shown; raise size or pass a subdirectory." if more else ""
    )
    return (
        excerpt.text.replace(
            header, f"{header}: {len(excerpt.files)} of {excerpt.candidates} file(s)", 1
        )
        + tail
    )
