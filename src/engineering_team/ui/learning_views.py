"""What the learning screens show: a run's harness and lessons, a repository's memory, the
experience library. The page only lays these out; everything is read and worded here.

Everything in them is learned or repository-supplied text (a skill from a repository, a remembered
fact, a lesson): the page shows it as data, never as markup or instructions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engineering_team.harness import HarnessError
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import recorded_domain, resolve_for_run
from engineering_team.learning.lessons import Lesson
from engineering_team.learning.library import LessonLibrary, libraries
from engineering_team.learning.retrieve import library_root, recorded
from engineering_team.memory import Fact, MemoryFileError, MemoryStore
from engineering_team.modes.isolation import read_isolation
from engineering_team.runtime.run_index import RunRef
from engineering_team.settings import Settings


def project_of(ref: RunRef) -> Path:
    """The project a run is about: the directory the team was made from, else its workspace."""

    isolation = read_isolation(ref.workspace)
    return isolation.source if isolation is not None else ref.workspace


# -- memory ---------------------------------------------------------------------------------------


def memory_store(root: Path, settings: Settings) -> MemoryStore:
    return MemoryStore(root, enabled=settings.memory.enabled, max_facts=settings.memory.max_facts)


def fact_row(fact: Fact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "kind": fact.kind,
        "status": fact.status,
        "topic": fact.topic,
        "text": fact.text,
        "run_id": fact.evidence.run_id,
        "check_ids": list(fact.evidence.check_ids),
        "sources": [s.path for s in fact.sources],
        "created": fact.created.isoformat(),
        "last_used": fact.last_used.isoformat() if fact.last_used else None,
    }


def memory_view(root: Path, settings: Settings) -> dict[str, Any]:
    """The repository's remembered facts (those whose source files changed marked stale first)."""

    store = memory_store(root, settings)
    problems: list[str] = []
    facts: list[Fact] = []
    try:
        store.refresh()
        facts = store.facts()
    except MemoryFileError as exc:
        problems = str(exc).splitlines()
    rows = [fact_row(f) for f in sorted(facts, key=lambda f: (f.status != "verified", f.kind))]
    counts = {s: sum(1 for f in facts if f.status == s) for s in ("verified", "proposed", "stale")}
    return {
        "repo": str(root),
        "enabled": settings.memory.enabled,
        "facts": rows,
        "counts": counts,
        "problems": problems,
        "note": "Facts the team verified about this repository. Only verified ones whose files "
        "have not changed reach a prompt; forget any you do not trust.",
    }


# -- lessons -----------------------------------------------------------------------------------


def lesson_row(lesson: Lesson, library: str, *, pending: bool) -> dict[str, Any]:
    evidence = lesson.evidence
    return {
        "id": lesson.id,
        "domain": lesson.domain,
        "cue": lesson.cue,
        "lesson": lesson.lesson,
        "component": lesson.component,
        "confidence": lesson.confidence,
        "library": library,
        "pending": pending,
        "created": lesson.created.isoformat(),
        "evidence": {
            "run_id": evidence.run_id,
            "verdict": evidence.verdict,
            "check_ids": list(evidence.check_ids),
            "rows": [
                {"behaviour": r.behaviour, "effect": r.effect, "verdict": r.verdict}
                for r in evidence.rows
            ],
        },
    }


def lesson_libraries(root: Path | None, settings: Settings) -> list[LessonLibrary]:
    return libraries(root, max_lessons=settings.learning.max_lessons)


def lessons_view(root: Path | None, settings: Settings) -> dict[str, Any]:
    """Every lesson of the user's library and, with ``root``, the repository's: those later
    runs receive, and those waiting for approval (``learning.review = "manual"``)."""

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    found = lesson_libraries(root, settings)
    for library in found:
        for pending in (False, True):
            lessons, notes = library.load(pending=pending)
            problems += notes
            rows += [lesson_row(item, library.name, pending=pending) for item in lessons]
    rows.sort(key=lambda r: (not r["pending"], r["domain"], r["created"]))
    return {
        "enabled": settings.learning.enabled,
        "review": settings.learning.review,
        "libraries": [{"name": lib.name, "root": str(lib.root)} for lib in found],
        "lessons": rows,
        "pending": sum(1 for r in rows if r["pending"]),
        "problems": problems,
    }


def find_library(
    root: Path | None, settings: Settings, ident: str, library: str | None
) -> LessonLibrary | None:
    """The library holding the lesson ``ident`` (``library`` narrows it to ``user`` or
    ``repository``)."""

    for candidate in lesson_libraries(root, settings):
        if library not in (None, candidate.name):
            continue
        if candidate.find(ident) is not None:
            return candidate
    return None


# -- a run -------------------------------------------------------------------------------------


def harness_view(ref: RunRef) -> dict[str, Any]:
    """The harness the run resolved: its domain, the packs, and the skills (those it loaded
    marked). Descriptions come from resolving the same domain again over today's files, so a pack
    edited since is shown as it is now (the manifest's digests say whether it changed)."""

    record = ref.manifest.harness or {}
    listed = list(record.get("skills_listed") or [])
    loaded = set(record.get("skills_loaded") or [])
    view: dict[str, Any] = {
        "recorded": bool(record.get("domain")),  # a run of 0.2.0 recorded no harness
        "domain": record.get("domain") or "",
        "packs": [
            {"name": p.get("name"), "source": p.get("source"), "sha256": p.get("sha256")}
            for p in record.get("packs") or []
        ],
        "skills": [
            {"name": name, "loaded": name in loaded, "description": "", "source": "", "pack": ""}
            for name in listed
        ],
        "loaded": sorted(loaded),
        "note": "",
    }
    if not view["recorded"]:
        view["note"] = "This run was made before runs recorded their harness."
        return view
    try:
        domain = recorded_domain(ref.run_dir) or Domain()
        resolved = resolve_for_run(domain, repo_root=ref.workspace)
    except HarnessError as exc:
        view["note"] = f"The harness cannot be resolved again now ({exc})."
        return view
    by_name = {s.name: s for s in resolved.skills.values()}
    for row in view["skills"]:
        skill = by_name.get(row["name"])
        if skill is not None:
            row.update(description=skill.description, source=skill.source, pack=skill.pack)
        else:
            row["description"] = "(no longer in the harness)"
    return view


def run_lessons(ref: RunRef, settings: Settings) -> dict[str, Any]:
    """The lessons the run was given at its start (``lessons.json``), as they are now."""

    ids = [str(i) for i in recorded(ref.run_dir).get("ids") or []]
    known: dict[str, dict[str, Any]] = {}
    if ids:
        for library in lesson_libraries(library_root(ref.workspace), settings):
            lessons, _ = library.load()
            known.update(
                {item.id: lesson_row(item, library.name, pending=False) for item in lessons}
            )
    return {
        "given": [known.get(i) or {"id": i, "gone": True} for i in ids],
        "note": "Distilled from earlier verified runs; the team weighs them as advice, "
        "never as instructions.",
    }


def run_learning(ref: RunRef, settings: Settings) -> dict[str, Any]:
    """Everything the run page's learning tab shows."""

    project = project_of(ref)
    return {
        "project": str(project),
        "harness": harness_view(ref),
        "lessons": run_lessons(ref, settings),
        "memory": memory_view(project, settings),
    }
