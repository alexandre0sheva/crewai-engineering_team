"""The controller's half of triage: rules that override the model where signals are certain, and
the signals-only guess used when there is no model answer.

The model's answer (:class:`TriageAnswer`) is agent-authored text, so nothing in it is trusted
as evidence: intents the repository cannot support are dropped, focus paths are kept only when
they exist, questions are capped, and an explicit mode always wins. Each override is recorded on
the profile (``overrides``), one line each.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ConfigDict

from engineering_team.contracts import (
    MAX_TRIAGE_QUESTIONS,
    Confidence,
    Intent,
    Risk,
    TaskProfile,
    TaskScale,
    TaskTarget,
    VerifyBy,
)
from engineering_team.intake.signals import MAX_PATHS, Signals, existing_path

INTENTS: tuple[str, ...] = get_args(Intent)
MAX_INTENTS = 3
MAX_TEXT = 300  # characters kept of each question, assumption, or the rationale

# The intent a 0.2.0 mode or maintenance preset stands for (``--mode`` wins over the model).
MODE_INTENTS: dict[str, Intent] = {
    "new": "new_project",
    "feature": "feature",
    "fix": "bugfix",
    "review": "review",
    "analyze": "question",
    "add-tests": "tests",
    "refactor": "refactor",
    "upgrade-deps": "upgrade_deps",
    "docs": "docs",
    "security-audit": "security_audit",
}
# Work that changes or judges code that must already exist.
EXISTING_ONLY: frozenset[str] = frozenset(
    {"bugfix", "refactor", "review", "upgrade_deps", "security_audit", "performance"}
)
# Checks that run the project's own commands: nothing to run without a project.
NEEDS_PROJECT: frozenset[str] = frozenset({"repro", "project_tests"})
DEFAULT_VERIFY: dict[str, list[VerifyBy]] = {
    "question": ["citations"],
    "research": ["citations"],
    "plan_only": ["review"],
    "review": ["review"],
    "bugfix": ["repro", "project_tests"],
    "feature": ["acceptance_tests", "project_tests"],
    "new_project": ["acceptance_tests", "build"],
    "refactor": ["project_tests"],
    "tests": ["project_tests"],
    "docs": ["review"],
    "upgrade_deps": ["project_tests", "build"],
    "security_audit": ["review"],
    "performance": ["project_tests"],
    "devops": ["build"],
}
# The signals-only guess: the first keyword group present decides, in this order.
_PRECEDENCE: tuple[Intent, ...] = (
    "plan_only", "security_audit", "upgrade_deps", "refactor", "performance", "research",
    "tests", "docs", "devops", "bugfix", "new_project", "feature",
)  # fmt: skip
_SECONDARY: tuple[Intent, ...] = ("tests", "docs", "devops")


class TriageAnswer(BaseModel):
    """What the model is asked to return. Every field is required and has no default, so the
    schema is a valid strict structured-output schema for every provider."""

    model_config = ConfigDict(extra="ignore")

    intents: list[Intent]
    size: TaskScale
    risk: Risk
    verify_by: list[VerifyBy]
    focus_paths: list[str]
    needs_clarification: bool
    questions: list[str]
    assumptions: list[str]
    confidence: Confidence
    rationale: str


def _text(value: str) -> str:
    return " ".join(value.split())[:MAX_TEXT]


def _distinct(values: list[str], limit: int) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        cleaned = _text(value)
        if cleaned and cleaned.lower() not in seen:
            seen[cleaned.lower()] = cleaned
    return list(seen.values())[:limit]


def apply_rules(
    answer: TriageAnswer, signals: Signals, *, mode: str | None = None, source: str = "model"
) -> TaskProfile:
    """The profile: the model's answer, corrected where the signals are certain."""

    overrides: list[str] = []
    intents: list[Intent] = list(dict.fromkeys(answer.intents))[:MAX_INTENTS]
    if not signals.existing_code:
        dropped = [i for i in intents if i in EXISTING_ONLY]
        if dropped:
            intents = [i for i in intents if i not in EXISTING_ONLY]
            overrides.append(f"no existing code: dropped {', '.join(dropped)} (needs a repository)")
        if not intents:
            intents = ["question" if signals.question else "new_project"]
            overrides.append(f"no existing code: primary intent is {intents[0]}")
    if not intents:
        intents = [guess_intent(signals)]
        overrides.append(f"the model gave no intent: {intents[0]} from the signals")
    if mode is not None and mode in MODE_INTENTS:
        wanted = MODE_INTENTS[mode]
        if intents[0] != wanted:
            overrides.append(f"--mode {mode}: primary intent {wanted} (model: {intents[0]})")
            intents = [wanted, *(i for i in intents if i != wanted)][:MAX_INTENTS]

    verify = list(dict.fromkeys(answer.verify_by)) or list(DEFAULT_VERIFY[intents[0]])
    if not signals.existing_code and NEEDS_PROJECT & set(verify):
        verify = [v for v in verify if v not in NEEDS_PROJECT] or ["acceptance_tests"]
        overrides.append("no existing code: no reproduction or project tests to verify with")

    focus = list(signals.paths)
    claimed = [p for p in answer.focus_paths if p.strip()]
    if signals.repo is not None and claimed:
        root = Path(signals.repo)
        real = [path for path in (existing_path(p, root) for p in claimed) if path]
        focus += real
        if len(real) < len(claimed):
            overrides.append(f"dropped {len(claimed) - len(real)} focus path(s) that do not exist")
    focus = sorted(dict.fromkeys(focus))[:MAX_PATHS]

    questions = _distinct(answer.questions, MAX_TRIAGE_QUESTIONS)
    if len(answer.questions) > MAX_TRIAGE_QUESTIONS:
        overrides.append(f"kept the first {MAX_TRIAGE_QUESTIONS} questions")
    clarify = answer.needs_clarification and bool(questions)
    if answer.needs_clarification and not questions:
        overrides.append("needs clarification without a question: not asked")

    return TaskProfile(
        intents=intents,
        target=TaskTarget(
            kind="existing" if signals.existing_code else "new",
            repo=signals.repo,
            focus_paths=focus,
        ),
        size=answer.size,
        risk=answer.risk,
        verify_by=verify,
        stack=signals.stacks or signals.languages,
        needs_clarification=clarify,
        questions=questions,
        assumptions=_distinct(answer.assumptions, 10),
        confidence=answer.confidence,
        rationale=_text(answer.rationale),
        overrides=overrides,
        source="signals" if source == "signals" else "model",
    )


def guess_intent(signals: Signals) -> Intent:
    """The primary intent from the signals alone (the fallback when no model answers)."""

    hits = signals.keywords
    if signals.diff_ref and "review" in hits:
        return "review"
    if signals.question and not ("plan_only" in hits or "research" in hits):
        return "question"
    if signals.stack_trace or signals.failing_tests:
        return "bugfix" if signals.existing_code else "question"
    for intent in _PRECEDENCE:
        if intent in hits and (signals.existing_code or intent not in EXISTING_ONLY):
            if intent == "feature" and not signals.existing_code:
                return "new_project"
            return intent
    if "review" in hits and signals.existing_code:
        return "review"
    return "feature" if signals.existing_code else "new_project"


def heuristic(signals: Signals) -> TriageAnswer:
    """A complete answer from the signals alone, with low confidence."""

    primary = guess_intent(signals)
    secondary = [i for i in _SECONDARY if i in signals.keywords and i != primary]
    words = signals.words
    size: TaskScale
    if primary in ("question", "research", "plan_only", "review"):
        size = "trivial" if words < 40 else "small"
    elif words > 600:
        size = "large"
    elif words > 150 or (primary == "new_project" and words > 60):
        size = "medium"
    else:
        size = "small"
    risk: Risk = "low"
    if signals.risk_terms or primary == "security_audit":
        risk = "high"
    elif primary in ("upgrade_deps", "refactor", "performance"):
        risk = "medium"
    return TriageAnswer(
        intents=[primary, *secondary][:MAX_INTENTS],
        size=size,
        risk=risk,
        verify_by=list(DEFAULT_VERIFY[primary]),
        focus_paths=[],
        needs_clarification=False,
        questions=[],
        assumptions=[],
        confidence="low",
        rationale="Guessed from the deterministic signals only (no model answer).",
    )
