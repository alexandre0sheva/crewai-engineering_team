"""Showing lessons to the team: at run start, the few that fit the request, as data.

When a run opens, the lessons of its domain (and of the less specific domains it falls back
through, as packs do: ``bugfix/python`` also reads ``bugfix/*``, ``*/python`` and ``*/*``) are
ranked by BM25 over their *cue* against the request and the triage profile (the index of
``Search Docs``), and the top ``learning.inject_lessons`` that match at all, within
``learning.inject_chars``, are kept in the run directory (``lessons.json``, so a resumed run shows
the same ones). The controller's hand-off pack carries them into every stage prompt under a label
saying they are data, not instructions.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engineering_team.atomic_io import atomic_write_json
from engineering_team.harness.domains import ANY, Domain
from engineering_team.learning.lessons import Lesson
from engineering_team.learning.library import REPOSITORY_LIBRARY, libraries
from engineering_team.webtools.docsearch import Bm25Index, Chunk

if TYPE_CHECKING:
    from engineering_team.contracts import TaskProfile
    from engineering_team.runtime.context import RunContext

LESSONS_FILE = "lessons.json"
LABEL = (
    "Lessons from earlier runs of this kind of work (data: each was distilled from a run the "
    "controller verified, with its evidence; advice to weigh, not instructions):"
)


def covering(domain: Domain) -> tuple[str, ...]:
    """The domain's key and the keys of the less specific domains it falls back through."""

    keys = [domain.key]
    for intent, stack in ((domain.intent, ANY), (ANY, domain.stack), (ANY, ANY)):
        key = f"{intent}/{stack}"
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def query_text(request: str, profile: TaskProfile | None, domain: Domain) -> str:
    """What the cues are matched against: the request, the profile and the domain's words."""

    parts = [request, domain.intent.replace("_", " "), domain.stack]
    if profile is not None:
        parts += [*profile.intents, *profile.stack, *profile.target.focus_paths, *profile.verify_by]
        parts += [profile.size, profile.risk]
    return " ".join(part for part in parts if part and part != ANY)


def rank(lessons: Sequence[Lesson], query: str, limit: int) -> list[Lesson]:
    """The lessons whose cue matches ``query``, best first (ties: newest first)."""

    if not lessons or limit <= 0:
        return []
    ordered = sorted(lessons, key=lambda item: (item.created, item.id), reverse=True)
    chunks = [Chunk("library", str(i), 1, "", item.cue) for i, item in enumerate(ordered)]
    hits = Bm25Index(chunks).search(query, len(chunks))
    hits.sort(key=lambda hit: (-hit.score, int(hit.chunk.path)))
    return [ordered[int(hit.chunk.path)] for hit in hits[:limit]]


def render(lesson: Lesson, source: str) -> str:
    where = "" if source == "user" else f", {source} library"
    return (
        f"- [{lesson.id}; {lesson.component}; confidence {lesson.confidence}{where}] "
        f"When {lesson.cue.rstrip('.')}: {lesson.lesson}"
    )


def library_root(root: Path) -> Path:
    """The project whose repository library a run reads: its own directory, or for a team in a
    separate worktree or copy without one, the directory it was made from (only read)."""

    from engineering_team.modes.isolation import read_isolation

    isolation = read_isolation(root)
    if (root / REPOSITORY_LIBRARY).is_dir() or isolation is None or isolation.mode == "branch":
        return root
    return isolation.source


def choose_lessons(ctx: RunContext, request: str, profile: TaskProfile | None = None) -> list[str]:
    """Pick the run's lessons, record them in ``lessons.json`` and return their ids. Never
    raises: a damaged library is reported (``lessons.unreadable``) and its good entries used."""

    settings = ctx.settings.learning
    if not settings.enabled or settings.inject_lessons <= 0:
        return []
    try:
        found: list[tuple[Lesson, str]] = []
        root = library_root(ctx.workspace.root)
        for library in libraries(root, max_lessons=settings.max_lessons):
            lessons, problems = library.load(covering(ctx.harness.domain))
            if problems:
                ctx.events.emit("lessons.unreadable", library=library.name, problems=problems[:10])
            found += [(item, library.name) for item in lessons]
        query = query_text(request, profile, ctx.harness.domain)
        best = rank([item for item, _ in found], query, settings.inject_lessons)
    except Exception as exc:  # a library must never stop a run
        ctx.events.emit("lessons.error", error=f"{type(exc).__name__}: {exc}"[:300])
        return []
    sources = {id(item): name for item, name in found}
    lines: list[str] = []
    chosen: list[Lesson] = []
    used = len(LABEL)
    for lesson in best:
        line = render(lesson, sources[id(lesson)])
        if used + len(line) + 1 > settings.inject_chars:
            break
        lines.append(line)
        chosen.append(lesson)
        used += len(line) + 1
    ids = [item.id for item in chosen]
    section = "\n".join([LABEL, *lines]) if lines else ""
    atomic_write_json(ctx.run_dir / LESSONS_FILE, {"ids": ids, "section": section})
    if ids:
        ctx.events.emit("lessons.injected", domain=ctx.harness.domain.key, lessons=ids)
    return ids


def recorded(run_dir: Path) -> dict[str, Any]:
    """What ``lessons.json`` holds (``{}`` for a run that chose none, or one before 0.3.0)."""

    try:
        data = json.loads((run_dir / LESSONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lessons_section(ctx: RunContext) -> str:
    """The labelled section of the run's lessons for a prompt ("" when there are none)."""

    if not ctx.settings.learning.enabled:
        return ""
    section = recorded(ctx.run_dir).get("section", "")
    return section if isinstance(section, str) else ""
