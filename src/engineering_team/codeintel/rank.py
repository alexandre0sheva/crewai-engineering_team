"""Ranking the files of a repository for one request: a personalised PageRank over the import graph.

The request, the triage focus paths and the reproduction say where the work starts (``seeds.py``):
files named in them (a stack trace's frames, a test path), files that define a symbol they mention
and files whose words match theirs seed the walk. Probability then flows along the import graph in
both directions (a file passes more to what it imports than to what imports it), so the modules
around the seeds (the code a crashing frame calls, the tests that import the module) rank high
even when the request never names them. A file's score is divided by its general importance, so
that hubs everything imports do not win on every request, and recently changed files (Git
history) are lifted a little. Static and best-effort, like the rest of
:mod:`engineering_team.codeintel`; deterministic for the same input.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from engineering_team.codeintel.git import Commit
from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.lexical import lexical as lexical_scores
from engineering_team.codeintel.seeds import Seeds

DAMPING = 0.85
ITERATIONS = 60
TOLERANCE = 1e-10
REVERSE_WEIGHT = 0.5  # what an imported file passes back to its importers, against 1.0 forwards
PATH_SHARE = 0.45  # of the restart mass: files named, symbols mentioned, words matched
SYMBOL_SHARE = 0.15
TERM_SHARE = 0.4
HUB_DAMPING = 0.5  # a file's score is divided by its general importance to this power
RECENCY_BOOST = 0.25  # a file changed today ranks up to this much higher


@dataclass(frozen=True)
class Ranked:
    """A file, its score (best = 1.0) and why it ranks where it does."""

    path: str
    score: float
    why: tuple[str, ...] = ()


def recency_weights(commits: Iterable[Commit], now: float, days: int) -> dict[str, float]:
    """1.0 for a file changed just now, halving every ``days / 4`` days, nothing beyond ``days``."""

    if days <= 0:
        return {}
    half_life = days / 4
    weights: dict[str, float] = {}
    for commit in commits:
        age = max(0.0, (now - commit.when) / 86_400)
        if age > days:
            continue
        value = 0.5 ** (age / half_life)
        for change in commit.files:
            weights[change.path] = max(weights.get(change.path, 0.0), value)
    return weights


def _edges(index: SourceIndex, graph: ImportGraph) -> dict[str, dict[str, float]]:
    edges: dict[str, dict[str, float]] = {path: {} for path in index.files}
    for path in index.files:
        for ref in graph.imports_of(path):
            for target in ref.targets:
                if target != path and target in edges:
                    edges[path][target] = 1.0
                    edges[target].setdefault(path, REVERSE_WEIGHT)
    return edges


def _personalisation(
    index: SourceIndex, seeds: Seeds, lexical: Mapping[str, float]
) -> dict[str, float]:
    """The restart distribution: each kind of evidence (files named, symbols mentioned, words
    matched) gets a fixed share, split among its files, so that a long stack trace cannot
    drown out what the person wrote."""

    classes: list[tuple[float, dict[str, float]]] = []
    if seeds.paths:
        classes.append((PATH_SHARE, dict(seeds.paths)))
    if seeds.symbols:
        defining: dict[str, set[str]] = defaultdict(set)
        for definition in index.all_definitions():
            if definition.name in seeds.symbols:
                defining[definition.name].add(definition.path)
        symbol_mass: dict[str, float] = defaultdict(float)
        for paths in defining.values():
            for path in paths:
                symbol_mass[path] += 1 / len(paths)
        classes.append((SYMBOL_SHARE, dict(symbol_mass)))
    if lexical:
        classes.append((TERM_SHARE, dict(lexical)))
    mass = dict.fromkeys(index.files, 0.0)
    shares = sum(share for share, found in classes if found)
    for share, found in classes:
        total = sum(found.values())
        if total <= 0:
            continue
        for path, value in found.items():
            mass[path] += share / shares * value / total
    if not shares:
        return dict.fromkeys(index.files, 1 / len(index.files))
    return mass


def _walk(edges: Mapping[str, Mapping[str, float]], start: Mapping[str, float]) -> dict[str, float]:
    """PageRank with restarts to ``start``; mass on files with no edges goes back to it."""

    rank = dict(start)
    for _ in range(ITERATIONS):
        spread = {path: (1 - DAMPING) * value for path, value in start.items()}
        stranded = 0.0
        for path in sorted(edges):
            out = edges[path]
            if not out:
                stranded += rank[path]
                continue
            share = DAMPING * rank[path] / sum(out.values())
            for target in sorted(out):
                spread[target] += share * out[target]
        for path, value in start.items():
            spread[path] += DAMPING * stranded * value
        moved = sum(abs(spread[path] - rank[path]) for path in rank)
        rank = spread
        if moved < TOLERANCE:
            break
    return rank


def rank_files(
    index: SourceIndex,
    graph: ImportGraph,
    seeds: Seeds,
    recency: Mapping[str, float] | None = None,
) -> list[Ranked]:
    """Every indexed file, best first (ties by path), scaled so that the best scores 1.0."""

    if not index.files:
        return []
    weights, matched = lexical_scores(index, seeds.terms)
    edges = _edges(index, graph)
    walked = _walk(edges, _personalisation(index, seeds, weights))
    general = _walk(edges, dict.fromkeys(index.files, 1 / len(index.files)))
    recent = recency or {}
    boosted = {
        path: walked[path]
        / max(general[path], 1e-12) ** HUB_DAMPING
        * (1 + RECENCY_BOOST * recent.get(path, 0.0))
        for path in walked
    }
    top = max(boosted.values()) or 1.0
    reasons = _reasons(index, edges, seeds, matched, recent)
    ranked = [Ranked(path, boosted[path] / top, reasons.get(path, ())) for path in boosted]
    return sorted(ranked, key=lambda item: (-round(item.score, 12), item.path))


def _reasons(
    index: SourceIndex,
    edges: Mapping[str, Mapping[str, float]],
    seeds: Seeds,
    matched: Mapping[str, str],
    recent: Mapping[str, float],
) -> dict[str, tuple[str, ...]]:
    names: dict[str, list[str]] = defaultdict(list)
    if seeds.symbols:
        for definition in index.all_definitions():
            if definition.name in seeds.symbols and definition.name not in names[definition.path]:
                names[definition.path].append(definition.name)
    reasons: dict[str, tuple[str, ...]] = {}
    for path in index.files:
        why: list[str] = []
        if path in seeds.paths:
            why.append("named in the request")
        if names.get(path):
            why.append("defines " + ", ".join(f"`{n}`" for n in names[path][:2]))
        if path in matched:
            why.append(f"mentions '{matched[path]}'")
        if not why:
            near = sorted(other for other in edges[path] if other in seeds.paths)
            if near:
                why.append(f"next to {near[0]} in the import graph")
        if recent.get(path, 0.0) >= 0.5:
            why.append("changed recently")
        reasons[path] = tuple(why[:3])
    return reasons
