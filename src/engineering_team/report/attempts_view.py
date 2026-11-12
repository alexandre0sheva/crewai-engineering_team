"""The report's "Attempts" section: every attempt of a best-of-N run, its checks and cost, which
one was chosen and why, and the cost ledger (``pipeline/attempts_contracts.py``)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from engineering_team.pipeline.attempts_contracts import AttemptsRecord, Selection
from engineering_team.report.fmt import money
from engineering_team.report.html_common import badge, esc, section, table

METHOD = {
    "judge": "chosen by the blind judge",
    "only": "the only attempt that passed",
    "fallback": "the smallest passing diff (the judge's answer was not used)",
    "none-passed": "no attempt passed: the closest is kept, unverified",
    "stopped": "the attempts did not run to the end",
}
TONE = {"passed": "good", "failed": "bad"}
HEAD = ["Attempt", "Varied by", "Result", "Required checks", "Findings", "Diff", "Cost", "Patch"]


def summary_line(record: AttemptsRecord) -> str:
    """One line: how many attempts, how the result was chosen, which one."""

    where = "the ladder's last rung" if record.source == "ladder" else "--attempts"
    selection = record.selection
    if selection is None:
        return f"Best of {record.count} ({where}), varied by {record.vary}: not finished."
    chosen = f": **{selection.chosen}**" if selection.chosen else ""
    return (
        f"Best of {record.count} ({where}), varied by {record.vary}; "
        f"{METHOD[selection.method]}{chosen}."
    )


def ledger_line(record: AttemptsRecord) -> str:
    """The cost ledger and whether it agrees with what the run's usage grew by."""

    ledger, spent = record.ledger_usd, record.spent_usd
    line = (
        f"Ledger: {len(record.attempts)} attempt(s) and the judge, {money(ledger)} in all; the "
        f"run's usage grew by {money(spent)} while they ran"
    )
    if ledger is not None and spent is not None:
        line += " (they agree)." if abs(ledger - spent) < 1e-6 else " (they DIFFER)."
    else:
        line += " (a model without a price: the identity cannot be checked)."
    estimate = f"; estimated beforehand at {money(record.estimate_usd)}"
    return line[:-1] + estimate + "."


def _rows(record: AttemptsRecord, wrap: Callable[[str], str]) -> list[list[str]]:
    selection = record.selection
    chosen = selection.chosen if selection else None
    rows = []
    for a in record.attempts:
        label = f"{a.label} (chosen)" if a.label == chosen else a.label
        anon = selection.anonymised.get(a.label) if selection else None
        if anon:
            label += f", candidate {anon}"
        checks = f"{a.checks_passed} of {a.checks_total}" if a.checks_total else "none"
        if a.failing_checks:
            checks += f"; failing: {', '.join(a.failing_checks)}"
        rows.append(
            [
                wrap(label),
                wrap(a.vary),
                wrap(a.outcome or a.status),
                wrap(checks),
                wrap(str(a.findings)),
                wrap(f"{a.files_changed} file(s), {a.diff_lines} line(s)"),
                wrap(money(a.cost_usd)),
                wrap(a.patch or "-"),
            ]
        )
    return rows


def _scores(selection: Selection) -> list[list[str]]:
    return [
        [
            f"{label} ({selection.anonymised.get(label, '?')})",
            str(s.correctness),
            str(s.minimality),
            str(s.consistency),
            str(s.tests),
            str(s.total),
        ]
        for label, s in sorted(selection.scores.items())
    ]


def notes(record: AttemptsRecord) -> list[str]:
    selection = record.selection
    notes: list[str] = []
    if selection is not None:
        if selection.reason:
            notes.append(f"Why: {selection.reason}")
        if selection.judge_model:
            notes.append(
                f"Judge: {selection.judge_model}, {money(selection.judge_cost_usd)}; it saw "
                "anonymous ids only, never which attempt, model or harness made a candidate."
            )
        if selection.revision_match is False:
            notes.append(
                "The run's tree after applying the chosen attempt is NOT the tree it was "
                "verified on."
            )
        elif selection.revision_match:
            notes.append("The run's tree after applying it is exactly the tree it was verified on.")
    if record.stopped:
        notes.append(f"Stopped: {record.stopped}")
    notes.append(ledger_line(record))
    return notes


MdTable = Callable[[Sequence[str], Sequence[Sequence[object]]], list[str]]


def markdown(
    record: AttemptsRecord | None, cell: Callable[[object], str], md_table: MdTable
) -> list[str]:
    """The section as Markdown lines (``cell`` and ``md_table`` are ``render_md``'s)."""

    if record is None:
        return []
    lines = ["", "## Attempts", "", summary_line(record), ""]
    lines += md_table(HEAD, _rows(record, cell))
    selection = record.selection
    if selection is not None and selection.scores:
        head = ["Candidate", "Correctness", "Minimality", "Consistency", "Tests", "Total"]
        lines += ["", *md_table(head, _scores(selection))]
    lines += ["", *(f"- {cell(note)}" for note in notes(record))]
    return lines


def html(record: AttemptsRecord | None) -> str:
    if record is None:
        return ""
    rows = _rows(record, esc)
    for row, attempt in zip(rows, record.attempts, strict=True):
        row[2] = badge(attempt.status, TONE.get(attempt.status, "info")) + " " + row[2]
    lead = f"<p>{esc(summary_line(record)).replace('**', '')}</p>"
    body = lead + table(HEAD, rows, (4, 6))
    selection = record.selection
    if selection is not None and selection.scores:
        head = ["Candidate", "Correctness", "Minimality", "Consistency", "Tests", "Total"]
        scores = [[esc(c) for c in row] for row in _scores(selection)]
        body += "<h3>Judge's scores</h3>" + table(head, scores, (1, 2, 3, 4, 5))
    body += "<ul>" + "".join(f"<li>{esc(note)}</li>" for note in notes(record)) + "</ul>"
    return section("attempts", "Attempts", body)
