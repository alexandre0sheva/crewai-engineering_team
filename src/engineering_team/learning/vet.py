"""The controller's check of a reflection: which lesson, if any, may enter the library.

A reflection is a model's reading of a run, so nothing it says is taken on trust. A proposed
lesson is kept only when every rule holds: the run's verdict is ``verified`` or ``failed`` with
checks the controller ran; the lesson cites behaviour rows that exist, at least one of which saw an
effect (``helped`` or ``hurt``; a lesson resting only on ``confounded`` or ``no observed effect``
rows is dropped); every check it cites is one the controller ran and every ``#seq`` a trajectory
entry; it names a component of the run's harness (a new skill may be named); and neither a secret
nor text of a protected file is in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.learning.evidence import RunEvidence
from engineering_team.learning.guard import Protected, guard
from engineering_team.learning.lessons import (
    COMPONENT,
    MAX_CUE_CHARS,
    MAX_LESSON_CHARS,
    MAX_ROW_CHARS,
    MAX_ROWS,
    BehaviourRow,
    Lesson,
    LessonEvidence,
    ReflectionAnswer,
    lesson_id,
)
from engineering_team.memory.facts import one_line

if TYPE_CHECKING:
    from engineering_team.harness.library import ResolvedHarness

EFFECTS = ("helped", "hurt")
SEQ = re.compile(r"#(\d+)")


@dataclass(frozen=True)
class Vetted:
    """The outcome: the lesson to store, or why there is none."""

    lesson: Lesson | None
    reason: str = ""


def clean_rows(rows: list[BehaviourRow]) -> list[BehaviourRow]:
    """At most ``MAX_ROWS`` rows, each text on one line and capped."""

    return [
        row.model_copy(
            update={
                "behaviour": one_line(row.behaviour, MAX_ROW_CHARS),
                "guidance": one_line(row.guidance, 120),
                "effect": one_line(row.effect, MAX_ROW_CHARS),
                "evidence": [one_line(item, 80) for item in row.evidence[:8]],
            }
        )
        for row in rows[:MAX_ROWS]
    ]


def component_problem(component: str, harness: ResolvedHarness) -> str | None:
    """Why ``component`` is not a component of this harness (``None`` when it is)."""

    if not COMPONENT.fullmatch(component):
        return (
            f"its component {component!r} is not a harness path (teammates/<key>/tool_guidance, "
            "stages/<name>, shared/<name> or skills/<name>)"
        )
    kind, _, rest = component.partition("/")
    name = rest.split("/", 1)[0]
    known: dict[str, set[str]] = {
        "teammates": set(harness.teammates),
        "stages": set(harness.stages),
        "shared": set(harness.shared),
    }
    if kind in known and name not in known[kind]:
        return f"its component {component!r} is not part of this run's harness"
    return None


def _unknown_seqs(rows: list[BehaviourRow], seqs: set[int]) -> list[str]:
    return [
        f"#{match}"
        for row in rows
        for item in row.evidence
        for match in SEQ.findall(item)
        if int(match) not in seqs
    ]


def vet(
    answer: ReflectionAnswer,
    evidence: RunEvidence,
    harness: ResolvedHarness,
    protected: Protected,
    secrets: tuple[str, ...] = (),
) -> Vetted:
    """The lesson the controller keeps from ``answer``, or why it keeps none."""

    proposed = answer.lesson
    if proposed is None:
        return Vetted(None, "NO_ENTRY")
    if evidence.verdict not in ("verified", "failed") or not evidence.has_checks:
        return Vetted(None, "the run has no verdict with checks behind it")
    rows = clean_rows(answer.rows)
    numbers = list(dict.fromkeys(proposed.evidence.rows))
    missing = [n for n in numbers if not 1 <= n <= len(rows)]
    if missing:
        return Vetted(None, f"it cites row(s) {missing} that the reflection does not have")
    cited = [rows[n - 1] for n in numbers]
    if not cited:
        return Vetted(None, "it cites no behaviour row")
    if all(row.verdict == "confounded" for row in cited):
        return Vetted(None, "it rests only on confounded rows")
    if not any(row.verdict in EFFECTS for row in cited):
        return Vetted(None, "no row it cites saw an effect (helped or hurt)")
    checks = list(dict.fromkeys(c.strip() for c in proposed.evidence.checks if c.strip()))
    checks += [
        item
        for row in cited
        for item in row.evidence
        if item in evidence.check_ids and item not in checks
    ]
    unknown = [c for c in proposed.evidence.checks if c.strip() not in evidence.check_ids]
    if unknown:
        return Vetted(None, f"it cites check(s) the controller never ran: {', '.join(unknown)}")
    if not checks:
        return Vetted(None, "it cites no check the controller ran")
    fabricated = _unknown_seqs(cited, evidence.seqs)
    if fabricated:
        return Vetted(None, f"it cites trajectory entries that do not exist: {fabricated[:3]}")
    component = proposed.component.strip()
    problem = component_problem(component, harness)
    if problem is not None:
        return Vetted(None, problem)
    cue = one_line(proposed.cue, MAX_CUE_CHARS)
    text = one_line(proposed.lesson, MAX_LESSON_CHARS)
    if not cue or not text:
        return Vetted(None, "its cue or its lesson is empty")
    texts = [cue, text, *(f"{r.behaviour} {r.effect} {r.guidance}" for r in cited)]
    reason = guard(texts, protected, secrets)
    if reason is not None:
        return Vetted(None, reason)
    lesson = Lesson(
        id=lesson_id(evidence.domain, cue, text),
        domain=evidence.domain,
        cue=cue,
        lesson=text,
        component=component,
        confidence=proposed.confidence,
        evidence=LessonEvidence(
            run_id=evidence.run_id,
            verdict=evidence.verdict,  # type: ignore[arg-type]
            check_ids=checks,
            revision=evidence.revision,
            rows=cited,
        ),
    )
    return Vetted(lesson)
