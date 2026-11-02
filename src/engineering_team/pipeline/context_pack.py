"""Context packs: what a stage is told about the repository before it starts.

A pack is three labelled sections of the controller's hand-off (``handoff.py``) that cost tokens
on every model call of the session: the repository's verified facts (``memory/inject.py``), the
lessons chosen for the run (``learning/retrieve.py``) and an excerpt of the ranked repo map: the
files most likely to matter for the request, from a personalised PageRank over the import graph
seeded by the request, the triage focus paths and the reproduction, lifted by Git recency
(``codeintel/rank.py``). The whole pack stays within ``context.stage_tokens`` (or the stage's own
entry in ``context.stages``): facts and lessons take at most a quarter each, the map the rest.
The skill list is not repeated here: it is part of the teammate's always-on text
(``harness/skills.py``), so a copy would be paid for twice.

Nothing in a pack is evidence. The map is the controller's static guess, labelled as such, and a
failure to build it never stops a stage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.codeintel.languages import language_of
from engineering_team.codeintel.repomap import (
    CHARS_PER_TOKEN,
    MapExcerpt,
    RankedFiles,
    build_ranking,
    recent_commits,
    render_map,
)
from engineering_team.learning.retrieve import lessons_section
from engineering_team.memory.inject import memory_section
from engineering_team.tools.ignore import iter_files

if TYPE_CHECKING:
    from engineering_team.pipeline.recipes import StageSpec
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext

REQUEST_FILE = "request.md"
PROFILE_FILE = "task_profile.json"  # the plan `do` kept with the run (``modes/threads.py``)
SECTION_SHARE = 0.25  # of the pack: the most the facts, and the most the lessons, may take
MIN_MAP_TOKENS = 100  # a map smaller than this is not worth its label
MAX_SCANNED = 5000
MAP_LABEL = (
    "Files most likely to matter for this request, ranked by the controller from the import "
    "graph, the request and Git history (a guide, not evidence: read the code before you rely "
    "on it):"
)
# A stage that reads the whole codebase in parts (the analysts of ``analyze``) must see it as it is.
NO_MAP_KINDS = frozenset({"analyze"})


@dataclass(frozen=True)
class ContextPack:
    """The pack of one stage session and what it holds."""

    text: str = ""
    budget: int = 0  # tokens
    sections: dict[str, int] = field(default_factory=dict)  # tokens by section
    files: tuple[str, ...] = ()  # the map's files, best first
    ranked: int = 0  # the files the map could have shown

    @property
    def tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def focus_paths(run_dir: Path) -> list[str]:
    """The paths triage found the request is about (``task_profile.json`` of a ``do`` run)."""

    try:
        data = json.loads((run_dir / PROFILE_FILE).read_text(encoding="utf-8"))
        found = data["profile"]["target"]["focus_paths"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [path for path in found if isinstance(path, str)] if isinstance(found, list) else []


def saved_task(run_dir: Path) -> tuple[str, str] | None:
    """The ``(intent, size)`` a ``do`` run was planned for, so a resumed run routes its models
    (``models.routes``) as the first one did; ``None`` for a run `do` did not plan."""

    try:
        profile = json.loads((run_dir / PROFILE_FILE).read_text(encoding="utf-8"))["profile"]
        intent, size = profile["intents"][0], profile["size"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    return (intent, size) if isinstance(intent, str) and isinstance(size, str) else None


def reproduction_files(state: PipelineState) -> list[str]:
    """Files the reproduction (the debugger's, the controller's or a stack trace's) points at."""

    found: list[str] = []
    if state.repro is not None:
        found += state.repro.files
    if state.fix is not None:
        found += [*state.fix.files, *state.fix.suspects]
    return list(dict.fromkeys(found))


def _fingerprint(ctx: RunContext, key: str) -> str:
    """The request and the set of source files: edits to a file do not change the ranking much,
    a new or removed one does."""

    names = []
    for count, path in enumerate(iter_files(ctx.workspace)):
        if count >= MAX_SCANNED:
            break
        name = ctx.workspace.relative_name(path)
        if language_of(name) is not None:
            names.append(name)
    return hashlib.sha256((key + "\n" + "\n".join(names)).encode()).hexdigest()


def ranked_files(ctx: RunContext, state: PipelineState) -> RankedFiles | None:
    """The workspace's files ranked for the run's request (cached; ``None`` when there is nothing
    to rank or the ranking failed, which is reported)."""

    settings = ctx.settings.context
    request = _read(ctx.run_dir / REQUEST_FILE)
    focus, repro = focus_paths(ctx.run_dir), reproduction_files(state)
    key = json.dumps([request, focus, repro, settings.recency_days])
    try:

        def build() -> RankedFiles:
            return build_ranking(
                ctx.workspace,
                request,
                focus_paths=focus,
                files=repro,
                commits=recent_commits(ctx),
                recency_days=settings.recency_days,
            )

        found: RankedFiles = ctx.rankings.get(_fingerprint(ctx, key), build)
    except Exception as exc:  # a map is a help; it must never stop a stage
        ctx.events.emit("context.error", error=f"{type(exc).__name__}: {exc}"[:300])
        return None
    return found if found.index.files else None


def _trim(section: str, max_chars: int) -> str:
    """``section`` (a label line, then one line per item) cut to whole lines in ``max_chars``."""

    if len(section) <= max_chars:
        return section
    lines = section.split("\n")
    kept = lines[:1]
    used = len(lines[0])
    for line in lines[1:]:
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept) if len(kept) > 1 else ""


def build_pack(
    ctx: RunContext, state: PipelineState, stage: StageSpec, paths: Sequence[str] = ()
) -> ContextPack:
    """The pack for a session of ``stage`` (``paths`` are its work package's)."""

    name = stage.prompt or stage.name
    budget = ctx.settings.context.tokens_for(name)
    room = budget * CHARS_PER_TOKEN
    side = int(room * SECTION_SHARE)
    parts: list[str] = []
    sections: dict[str, int] = {}

    def add(label: str, text: str) -> None:
        if text:
            parts.append(text)
            sections[label] = len(text) // CHARS_PER_TOKEN

    facts, _ = memory_section(ctx, name, paths, max_chars=side)
    add("facts", facts)
    add("lessons", _trim(lessons_section(ctx), side))
    used = sum(len(part) + 1 for part in parts)
    excerpt = MapExcerpt("", (), 0)
    left = (room - used) // CHARS_PER_TOKEN - len(MAP_LABEL) // CHARS_PER_TOKEN - 1
    if ctx.settings.context.enabled and stage.kind not in NO_MAP_KINDS and left >= MIN_MAP_TOKENS:
        found = ranked_files(ctx, state)
        if found is not None:
            excerpt = render_map(found, left)
            if excerpt.files:
                add("map", f"{MAP_LABEL}\n{excerpt.text}")
    pack = ContextPack("\n\n".join(parts), budget, sections, excerpt.files, excerpt.candidates)
    if pack.text:
        ctx.events.emit(
            "context.pack",
            stage=stage.name,
            budget_tokens=budget,
            tokens=pack.tokens,
            sections=sections,
            files=list(pack.files[:12]),
        )
    return pack
