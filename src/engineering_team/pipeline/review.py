"""The review stage's controller side: checking reviewers' findings, merging duplicates,
numbering them, deciding which are serious, and writing ``docs/review.md``.

Reviewers (read-only agents) only return a ``ReviewReport``. Nothing they claim is trusted
beyond being a claim: severity must be one of the contract's, a finding without text or with a
path outside the project is dropped, the reviewer's role is set by the controller, and what two
reviewers both found is reported once, at the higher severity, naming both.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from engineering_team.contracts import Finding, ReviewReport, Severity
from engineering_team.verification.revision import REVIEW_FILE as REVIEW_FILE

SEVERITY_ORDER: tuple[Severity, ...] = ("info", "low", "medium", "high", "critical")
RANK: dict[str, int] = {name: index for index, name in enumerate(SEVERITY_ORDER)}
SIMILAR = 0.5  # token overlap above which two findings on the same spot are one finding
LINE_SLACK = 3  # findings this close in the same file may be the same finding
MAX_SUMMARY = 400
MAX_FIX = 600
MAX_BRIEF = 6000
_WORDS = re.compile(r"[a-z0-9_]+")
_STOP_TEXT = "a an and are as at be but by for if in into is it of on or that the to was with"
STOP_WORDS = frozenset(_STOP_TEXT.split())


@dataclass
class Reviewed:
    """One reviewer's outcome: its report, or why it has none."""

    teammate: str
    report: ReviewReport | None = None
    error: str = ""
    dropped: list[str] = field(default_factory=list)  # findings the controller refused, and why


def clean_finding(finding: Finding, role: str) -> tuple[Finding | None, str]:
    """``finding`` as the controller will keep it (or ``None`` and why it was dropped)."""

    summary = " ".join(finding.summary.split())[:MAX_SUMMARY]
    if not summary:
        return None, "a finding with no summary"
    path = None
    if finding.file:
        posix = PurePosixPath(finding.file.replace("\\", "/").removeprefix("./"))
        if posix.is_absolute() or ".." in posix.parts or not posix.parts:
            return None, f"{summary[:60]!r}: the file {finding.file!r} is not inside the project"
        path = posix.as_posix()
    line = finding.line if finding.line is not None and finding.line >= 1 and path else None
    fix = " ".join((finding.suggested_fix or "").split())[:MAX_FIX] or None
    return (
        Finding(
            severity=finding.severity,
            summary=summary,
            file=path,
            line=line,
            suggested_fix=fix,
            source_role=role,
        ),
        "",
    )


def _tokens(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in STOP_WORDS and len(w) > 1}


def _same(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if a.line is not None and b.line is not None and abs(a.line - b.line) > LINE_SLACK:
        return False
    left, right = _tokens(a.summary), _tokens(b.summary)
    if not left or not right:
        return a.summary.lower() == b.summary.lower()
    return len(left & right) / len(left | right) >= SIMILAR


def _merge(kept: Finding, other: Finding) -> Finding:
    stronger = kept if RANK[kept.severity] >= RANK[other.severity] else other
    roles = list(dict.fromkeys([*(kept.source_role or "").split(", "), other.source_role or ""]))
    return stronger.model_copy(
        update={
            "source_role": ", ".join(r for r in roles if r),
            "suggested_fix": kept.suggested_fix or other.suggested_fix,
            "line": kept.line if kept.line is not None else other.line,
        }
    )


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """Findings with duplicates (same file, nearby lines, similar words) merged, in input order."""

    merged: list[Finding] = []
    for finding in findings:
        for index, known in enumerate(merged):
            if _same(known, finding):
                merged[index] = _merge(known, finding)
                break
        else:
            merged.append(finding)
    return merged


def consolidate(reviews: Sequence[Reviewed]) -> list[Finding]:
    """Every reviewer's valid findings, deduplicated, most serious first, numbered ``F-n``.

    The order is deterministic (severity, file, line, text) whatever order reviewers finished in.
    """

    cleaned: list[Finding] = []
    for review in reviews:
        if review.report is None:
            continue
        for raw in review.report.findings:
            kept, why = clean_finding(raw, review.teammate)
            if kept is None:
                review.dropped.append(why)
            else:
                cleaned.append(kept)
    merged = dedupe(cleaned)
    merged.sort(key=lambda f: (-RANK[f.severity], f.file or "", f.line or 0, f.summary))
    return [f.model_copy(update={"id": f"F-{n}"}) for n, f in enumerate(merged, start=1)]


def blocking(findings: Sequence[Finding], fail_on: str) -> list[Finding]:
    """The findings at or above the ``review.fail_on`` severity."""

    threshold = RANK[fail_on]
    return [f for f in findings if RANK[f.severity] >= threshold]


def where(finding: Finding) -> str:
    if not finding.file:
        return "(project-wide)"
    return f"{finding.file}:{finding.line}" if finding.line else finding.file


def repair_brief(findings: Sequence[Finding]) -> str:
    """What the repair agent is told about the findings it must fix."""

    lines = []
    for f in findings:
        fix = f" Suggested fix: {f.suggested_fix}" if f.suggested_fix else ""
        lines.append(f"- {f.id} [{f.severity}] {where(f)}: {f.summary}{fix}")
    text = "\n".join(lines)
    if len(text) <= MAX_BRIEF:
        return text
    return text[:MAX_BRIEF] + "\n... (more findings in docs/review.md)"


def render_review(
    findings: Sequence[Finding],
    reviews: Sequence[Reviewed],
    *,
    fail_on: str,
    outcome: str = "",
    extra: Sequence[str] = (),
) -> str:
    """``docs/review.md``: written by the controller from the validated findings."""

    lines = [
        "# Review",
        "",
        "Findings from read-only reviewers; the controller checked and merged them. Findings of "
        f"severity {fail_on} or higher are sent to repair, and the project is then verified "
        "again (it is not reviewed again).",
    ]
    lines += ["", "## Reviewers", ""]
    for r in reviews:
        if r.report is None:
            lines.append(f"- {r.teammate}: did not report ({r.error or 'no report'})")
        else:
            said = " ".join(r.report.summary.split())[:MAX_SUMMARY] or "no summary"
            lines.append(f"- {r.teammate}: {len(r.report.findings)} finding(s). {said}")
        lines += [f"  - dropped: {why}" for why in r.dropped]
    lines += ["", "## Findings", ""]
    if not findings:
        lines.append("No findings.")
    for f in findings:
        mark = " (sent to repair)" if RANK[f.severity] >= RANK[fail_on] else ""
        lines.append(
            f"- **{f.id}** [{f.severity}]{mark} `{where(f)}`: {f.summary} (from {f.source_role})"
        )
        if f.suggested_fix:
            lines.append(f"  - Suggested fix: {f.suggested_fix}")
    if extra:
        lines += ["", *extra]
    if outcome:
        lines += ["", "## Outcome", "", outcome]
    return "\n".join(lines) + "\n"
