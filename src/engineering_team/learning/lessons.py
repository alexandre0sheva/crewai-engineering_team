"""The data of the experience library: what a reflection answers, and the lessons it keeps.

A reflection (``reflect.py``) reads one verified run and answers with **behaviour rows** (what an
agent did differently from the harness's guidance, the effect the controller's records show, and a
verdict ``helped``, ``hurt``, ``no observed effect`` or ``confounded``) and at most **one lesson**
(a *cue*, the shape of situation it applies to; the *lesson*, an operation on a named harness
component; the rows and check ids it rests on; a confidence), or ``NO_ENTRY``. A stored lesson
carries the controller's evidence: the run, its verdict, the checks and the rows. Lessons are
untrusted data: they shape prompts and nothing else (``docs/HARNESS.md#lessons``).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engineering_team.contracts import utc_now

RowVerdict = Literal["helped", "hurt", "no observed effect", "confounded"]
ROW_VERDICTS: tuple[str, ...] = ("helped", "hurt", "no observed effect", "confounded")
Confidence = Literal["low", "medium", "high"]
LessonVerdict = Literal["verified", "failed"]  # the run verdicts a lesson may rest on
NO_ENTRY = "NO_ENTRY"

MAX_CUE_CHARS = 300
MAX_LESSON_CHARS = 600
MAX_ROW_CHARS = 300
MAX_ROWS = 8
# A harness component as a path of the harness tree (docs/HARNESS.md#components).
COMPONENT = re.compile(
    r"(teammates/[a-z][a-z0-9_]*/(tool_guidance|guardrails|role)"
    r"|stages/[a-z][a-z0-9_]*"
    r"|shared/[a-z][a-z0-9_]*"
    r"|skills/[a-z0-9][a-z0-9-]{0,63})"
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class BehaviourRow(BaseModel):
    """What an agent did differently from the harness's guidance, and what came of it."""

    model_config = ConfigDict(extra="ignore")

    behaviour: str = Field(description="What the agent did, differently from the guidance.")
    guidance: str = Field(default="", description="The harness component it departed from.")
    effect: str = Field(description="The effect the controller's records show.")
    verdict: RowVerdict
    evidence: list[str] = Field(
        default_factory=list, description="Check ids and trajectory entries (#seq) it rests on."
    )


class LessonEvidenceClaim(BaseModel):
    """What a proposed lesson says it rests on: row numbers (1-based) and check ids."""

    model_config = ConfigDict(extra="ignore")

    rows: list[int] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class ProposedLesson(BaseModel):
    """The lesson a reflection proposes (not yet vetted by the controller)."""

    model_config = ConfigDict(extra="ignore")

    cue: str = Field(description="The shape of situation the lesson applies to.")
    lesson: str = Field(description="The operation, naming the harness component.")
    component: str = Field(description="The harness component, e.g. stages/fix.")
    evidence: LessonEvidenceClaim = Field(default_factory=LessonEvidenceClaim)
    confidence: Confidence = "low"


class ReflectionAnswer(BaseModel):
    """The model's answer: behaviour rows, and one lesson or none (``NO_ENTRY``)."""

    model_config = ConfigDict(extra="ignore")

    rows: list[BehaviourRow] = Field(default_factory=list)
    lesson: ProposedLesson | None = None


class ReflectionError(ValueError):
    """The model's answer is not a usable reflection."""


class LessonEvidence(BaseModel):
    """The controller's record a stored lesson rests on."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    verdict: LessonVerdict
    check_ids: list[str] = Field(default_factory=list)
    revision: str = ""
    rows: list[BehaviourRow] = Field(default_factory=list)


class Lesson(BaseModel):
    """One lesson of the library (``<domain>/<id>/EXPERIENCE.md``)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    domain: str
    cue: str
    lesson: str
    component: str
    confidence: Confidence = "low"
    evidence: LessonEvidence
    created: datetime = Field(default_factory=utc_now)
    review: Literal["auto", "approved"] = "auto"


def lesson_id(domain: str, cue: str, text: str) -> str:
    """``les-<8 hex>`` from the domain and the lesson's words (the same lesson, the same id)."""

    words = "\0".join((domain, " ".join(cue.lower().split()), " ".join(text.lower().split())))
    return f"les-{hashlib.sha256(words.encode()).hexdigest()[:8]}"


def parse_reflection(raw: Any) -> ReflectionAnswer:
    """The model's reply as a :class:`ReflectionAnswer`: an instance, a mapping, JSON text (in a
    code fence or with words around it), or the bare word ``NO_ENTRY``. ``"lesson": "NO_ENTRY"``
    means no lesson. Raises :class:`ReflectionError`."""

    if isinstance(raw, ReflectionAnswer):
        return raw
    data: Any = raw.model_dump() if hasattr(raw, "model_dump") else raw
    if isinstance(data, str):
        text = _FENCE.sub("", data.strip())
        if text.strip().strip("\"'`.") == NO_ENTRY:
            return ReflectionAnswer()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ReflectionError(f"the answer has no JSON object: {data[:120]!r}")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ReflectionError(f"the answer is not valid JSON: {exc}") from None
    if isinstance(data, dict) and isinstance(data.get("lesson"), str):
        if data["lesson"].strip() != NO_ENTRY:
            raise ReflectionError('lesson: an object, null, or "NO_ENTRY"')
        data = {**data, "lesson": None}
    try:
        return ReflectionAnswer.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "answer"
        raise ReflectionError(f"{where}: {first['msg']} ({exc.error_count()} problem(s))") from None
