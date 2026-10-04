"""What the report says about the run's standing: the banner, the reasons, and the warnings.

All of it is derived from controller-owned records (the manifest, the check results, the
verification record). Agent-written text is only quoted, never used to decide a status.
"""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.board.models import Card
from engineering_team.contracts import (
    BudgetStatus,
    CheckResult,
    CriterionCoverage,
    Finding,
    RunManifest,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.report.digest import EventDigest
from engineering_team.report.model import Banner, Notice, Tone

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SERIOUS = ("critical", "high")
MAX_REASONS = 8


def _label(manifest: RunManifest) -> tuple[str, Tone]:
    verdict, status = manifest.verdict, manifest.status
    if verdict == "needs-info":
        return "Needs information", "warn"
    if verdict == "failed":
        return "Not verified", "bad"
    if verdict == "partial":
        return "Partially verified", "warn"
    labels: dict[str, tuple[str, Tone]] = {
        "succeeded": ("Verified", "good") if verdict == "verified" else ("Succeeded", "good"),
        "failed": ("Failed", "bad"),
        "cancelled": ("Cancelled", "warn"),
        "interrupted": ("Interrupted", "warn"),
        "running": ("Running", "info"),
        "pending": ("Pending", "info"),
    }
    return labels.get(status, (status.capitalize(), "info"))


def _last_note(card: Card) -> str:
    return next((move.note for move in reversed(card.history) if move.note), "")


def _reasons(
    manifest: RunManifest,
    state: PipelineState | None,
    checks: Sequence[CheckResult],
    cards: Sequence[Card],
    digest: EventDigest,
    budget: BudgetStatus | None,
) -> list[str]:
    reasons: list[str] = []
    if error := digest.error or (state.error if state else ""):
        reasons.append(error)
    if budget is not None and budget.exceeded:
        reasons.append(budget.exceeded)
    reasons += [
        f"Stage {stage.name} {stage.status}" + (f": {stage.detail}" if stage.detail else "")
        for stage in manifest.stages
        if stage.status in ("failed", "cancelled", "interrupted")
    ]
    if state is not None:
        reasons += state.verification.problems
        reasons += [f"Question for you: {question}" for question in state.needs_info]
    for check in checks:
        if check.required and check.status in ("failed", "unavailable"):
            said = f": {check.summary}" if check.summary else ""
            reasons.append(f"Required check {check.id} {check.status}{said}")
    for card in cards:
        if card.status == "failed" and card.kind != "check":
            why = card.blocked_reason or _last_note(card)
            reasons.append(f"Card {card.id} ({card.title}) failed" + (f": {why}" if why else ""))
    unique = list(dict.fromkeys(" ".join(reason.split()) for reason in reasons if reason.strip()))
    return unique[:MAX_REASONS]


def make_banner(
    manifest: RunManifest,
    state: PipelineState | None,
    checks: Sequence[CheckResult],
    cards: Sequence[Card],
    digest: EventDigest,
    budget: BudgetStatus | None,
) -> Banner:
    label, tone = _label(manifest)
    reasons = [] if tone == "good" else _reasons(manifest, state, checks, cards, digest, budget)
    return Banner(label, tone, reasons)


def make_notices(
    manifest: RunManifest,
    state: PipelineState | None,
    checks: Sequence[CheckResult],
    coverage: Sequence[CriterionCoverage],
    findings: Sequence[Finding],
    digest: EventDigest,
    budget: BudgetStatus | None,
    unpriced: Sequence[str],
) -> list[Notice]:
    notices: list[Notice] = []
    if budget is not None and budget.state in ("warning", "exceeded"):
        detail = "; ".join(f"{item.name} {item.fraction:.0%}" for item in budget.limits)
        tone: Tone = "bad" if budget.state == "exceeded" else "warn"
        notices.append(Notice(tone, f"Budget {budget.state}: {detail}"))
    if budget is not None:
        notices += [Notice("info", f"Budget note: {note}") for note in budget.notes]
    for check in checks:
        if check.status in ("unavailable", "skipped"):
            level: Tone = "warn" if check.required else "info"
            hint = f" ({check.hint})" if check.hint else ""
            notices.append(
                Notice(
                    level,
                    f"Check {check.id} was {check.status}"
                    + (" and is required" if check.required else "")
                    + hint,
                )
            )
    unverified = [item.id for item in coverage if item.status != "verified"]
    if unverified:
        notices.append(
            Notice(
                "warn",
                f"{len(unverified)} of {len(coverage)} acceptance criteria are not proven by a "
                f"passing check: {', '.join(unverified)}",
            )
        )
    fix = state.fix if state is not None else None
    if fix is not None and not fix.reproduced and fix.allow_unreproduced:
        notices.append(
            Notice(
                "warn",
                f"The bug was not reproduced ({fix.attempts} attempt(s)); the fix was made "
                "without a failing test (--allow-unreproduced).",
            )
        )
    serious = [item for item in findings if item.severity in SERIOUS]
    if serious:
        notices.append(Notice("warn", f"{len(serious)} high or critical review finding(s)."))
    noise = state.diff_noise if state is not None else None
    if noise is not None and noise.outside_files:
        notices.append(
            Notice(
                "warn",
                f"{noise.outside_files} changed file(s) are outside the plan's scope "
                f"({noise.outside_lines} of {noise.total_lines} changed lines).",
            )
        )
    if unpriced:
        notices.append(Notice("info", f"Cost is unknown: no price for {', '.join(unpriced)}."))
    if digest.repair_rounds:
        notices.append(Notice("info", f"{digest.repair_rounds} repair round(s) were needed."))
    if manifest.resumes:
        notices.append(Notice("info", f"The run was resumed {manifest.resumes} time(s)."))
    notices += [Notice("warn" if tone == "warn" else "info", text) for tone, text in digest.notices]
    return notices
