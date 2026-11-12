"""The new-run page's preview: what ``do`` would understand and plan for a request.

``POST /triage`` answers with this view. It is the plan of ``cli/do_plan.py`` (the same code the
terminal's preview uses) turned into the words and numbers the page shows, so the page computes
nothing. By default the signals alone decide (instant, free); ``refine`` asks the model's cheap
triage as well, which is what a run itself does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engineering_team.cli import do_plan
from engineering_team.harness.library import describe
from engineering_team.intake.triage import signal_lines
from engineering_team.pipeline.ladder import money

INTENT_LABELS = {
    "question": "A question about the project",
    "research": "Research",
    "review": "A review of a change",
    "bugfix": "A bug fix",
    "feature": "A new feature",
    "new_project": "A new project",
    "refactor": "A refactoring",
    "tests": "Adding tests",
    "docs": "Documentation",
    "upgrade_deps": "Upgrading dependencies",
    "security_audit": "A security audit",
    "performance": "Performance work",
    "devops": "DevOps and CI",
    "plan_only": "A plan (nothing is implemented)",
}
# What the person may force instead of triage (``do --mode``).
FORCE_LABELS = {
    "new": "Build a new project",
    "feature": "Add a feature",
    "fix": "Fix a bug",
    "add-tests": "Add tests",
    "refactor": "Refactor",
    "upgrade-deps": "Upgrade dependencies",
    "docs": "Write docs",
    "security-audit": "Security audit",
    "review": "Review a change",
    "answer": "Answer a question",
}
ROUTE_LABELS = {
    "new": "Builds a new project",
    "repository": "Changes your project, on its own branch",
    "review": "Reads your change; modifies nothing",
    "analyze": "Maps the project; modifies nothing",
    "answer": "Answers read-only, with checked citations",
}
RUNG_LABELS = {
    "solo": "One agent alone",
    "team": "A lead with teammates on call",
    "pipeline": "The full team, in stages",
    "attempts": "Several attempts, the best chosen blind",
}


def route_text(plan: do_plan.DoPlan) -> str:
    """The first sentence of the terminal's route line, without the lead-in and the colon that
    introduces the stage list the page shows as a table."""

    first = do_plan.route_line(plan).split("\n")[0].strip()
    first = re.sub(r"(?i)[;,.]?\s*stages( of each attempt)?:?$", "", first)
    return first.removeprefix("Plan: ").rstrip(".:") + "."


def view(plan: do_plan.DoPlan) -> dict[str, Any]:
    """The preview of ``plan`` as JSON-ready data (every string the page shows is made here)."""

    profile = plan.profile
    document = do_plan.plan_document(plan)  # raises HarnessError for a pack that does not load
    ladder = plan.ladder
    first, worst = plan.first_usd, plan.worst_usd
    return {
        "understood": {
            "headline": ", ".join(INTENT_LABELS.get(i, i) for i in profile.intents),
            "intents": list(profile.intents),
            "project": str(plan.root) if plan.root else None,
            "where": f"the project at {plan.root}" if plan.root else "a new project",
            "size": profile.size,
            "risk": profile.risk,
            "confidence": profile.confidence,
            "by": "signals only" if profile.source == "signals" else "the model",
            "rationale": profile.rationale,
            "proved_by": list(profile.verify_by),
            "assumptions": list(profile.assumptions),
            "rules": list(profile.overrides),
            "questions": list(profile.questions) if profile.needs_clarification else [],
        },
        "route": {
            "key": plan.route,
            "label": ROUTE_LABELS[plan.route],
            "text": route_text(plan),
        },
        "strategy": plan.strategy,
        "rung": None
        if ladder is None
        else {
            "start": ladder.start,
            "start_label": RUNG_LABELS[ladder.start],
            "max": ladder.max_rung,
            "max_label": RUNG_LABELS[ladder.max_rung],
            "reasons": list(ladder.reasons),
        },
        "attempts": {"count": plan.attempts, "vary": plan.vary},
        "size": plan.size,
        "recipe": plan.recipe.name if plan.recipe else None,
        "stages": document["stages"],
        "estimate": {
            "first_usd": first,
            "worst_usd": worst,
            "first": money(first),
            "worst": money(worst),
            "budget_usd": plan.budget_usd,
            "read_only": plan.route in ("review", "analyze", "answer"),
            "text": do_plan.cost_line(plan),
        },
        "harness": {
            "line": describe(do_plan.harness_of(plan)),
            "domain": document["harness"]["domain"],
        },
        "signals": signal_lines(plan.triage.signals),
        "notes": list(plan.notes),
        "triage_usage": document["triage_usage"],
        "model_used": plan.triage.usage is not None,
    }


def project_of(raw: str | None) -> Path | None:
    """The directory a preview is about (an absolute, existing one); ``ValueError`` otherwise."""

    if raw is None or not raw.strip():
        return None
    if "\0" in raw:
        raise ValueError("The project folder is not a path.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{raw!r} is not an absolute path.")
    if not path.is_dir():
        raise ValueError(f"{raw!r} is not an existing directory.")
    return path.resolve()
