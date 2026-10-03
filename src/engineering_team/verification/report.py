"""``docs/verification.md``, rendered by the controller from the check results.

Nothing in it comes from an agent's own report. It is reproducible: it carries no timestamps,
durations, run ids, or log paths (those are in the run directory), so the same results give the
same file, and a resumed run ends with the same tree as an uninterrupted one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from engineering_team.contracts import CheckResult, VerificationRecord
from engineering_team.verification.verdict import Judgement

STATUS_LABEL = {
    "passed": "PASSED",
    "failed": "FAILED",
    "unavailable": "UNAVAILABLE",
    "skipped": "SKIPPED",
}
EXPLANATION = {
    "verified": "Every required check passed on the project as it is now.",
    "failed": "A required check failed; the project is not verified.",
    "partial": "No required check failed, but some could not run; the project is not fully "
    "verified.",
}


def _cell(text: str) -> str:
    """Text for a table cell: one line, with pipes escaped."""

    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def _code(text: str) -> str:
    return f"`{_cell(text).replace('`', chr(39))}`" if text else ""


def _fence(text: str) -> str:
    """``text`` in a code block whose fence is longer than any run of backticks inside it."""

    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text.rstrip()}\n{fence}"


def render_report(
    record: VerificationRecord,
    results: Sequence[CheckResult],
    judgement: Judgement,
    *,
    max_rounds: int,
    notes: str = "docs/qa-notes.md",
) -> str:
    verdict = judgement.verdict
    lines = [
        "# Verification",
        "",
        "> Written by the controller from checks it ran itself. Nothing here is taken from an "
        f"agent's report; the agents' own notes are in `{notes}` and are not evidence.",
        "",
        f"**Verdict: {verdict.upper()}**: {EXPLANATION[verdict]}",
        "",
        f"Repair rounds used: {record.rounds} of {max_rounds}",
    ]
    if judgement.problems:
        lines += ["", *[f"- {problem}" for problem in judgement.problems]]

    lines += ["", "## Independent checks", ""]
    lines += ["| Check | Kind | Required | Status | Exit | Result | Command |"]
    lines += ["|---|---|---|---|---|---|---|"]
    for r in results:
        exit_code = "" if r.exit_code is None else str(r.exit_code)
        lines.append(
            f"| {_cell(r.id)} | {r.kind} | {'yes' if r.required else 'no'} | "
            f"{STATUS_LABEL[r.status]} | {exit_code} | {_cell(r.summary)} | {_code(r.command)} |"
        )
    for r in results:
        if r.status == "passed":
            continue
        lines += ["", f"### {r.id}: {STATUS_LABEL[r.status].lower()}", ""]
        if r.hint:
            lines += [r.hint, ""]
        if r.suspect_files:
            lines += [f"Files it points at: {', '.join(r.suspect_files)}", ""]
        if r.log_tail:
            lines.append(_fence(r.log_tail))

    known = [r for r in results if r.known_failures]
    if known:
        lines += ["", "## Known failures from the baseline", ""]
        lines.append(
            "These failed before the team changed anything. A check that shows only these is "
            "recorded as passed (no new failures); new failures are listed as such."
        )
        for r in known:
            lines += ["", f"### {_cell(r.id)}", ""]
            lines += [f"- known: {_cell(key)}" for key in r.known_failures]
            lines += [f"- NEW: {_cell(key)}" for key in r.new_failures]

    if record.coverage:
        lines += ["", "## Acceptance criteria", ""]
        lines += ["| ID | Criterion | Status | Checks / note |", "|---|---|---|---|"]
        for c in record.coverage:
            evidence = ", ".join(c.checks) if c.status == "verified" else c.note
            lines.append(f"| {_cell(c.id)} | {_cell(c.text)} | {c.status} | {_cell(evidence)} |")
        manual = [c for c in record.coverage if c.status != "verified"]
        lines += ["", "### Manual / unverified", ""]
        if manual:
            lines.append(
                "No passing check that is mapped to these criteria ran; a person has to confirm "
                "them."
            )
            lines += ["", *[f"- {c.id}: {_cell(c.text)}" for c in manual]]
        else:
            lines.append("None: every criterion is proven by a passing mapped check.")

    if record.repair_log:
        lines += ["", "## Repair rounds", "", *[f"- {entry}" for entry in record.repair_log]]
    if record.notes:
        lines += ["", "## Notes", "", *[f"- {note}" for note in record.notes]]
    return "\n".join(lines) + "\n"
