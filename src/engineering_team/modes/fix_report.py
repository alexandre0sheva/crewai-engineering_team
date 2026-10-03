"""The "fix" section of ``CHANGE_SUMMARY.md``: what the controller saw, then what the debugger said.

Red and green are the controller's own runs, recorded in the pipeline state and the event log
(``fix.red``, ``fix.green``); the root cause and the risk are the debugger's account and are
labelled as such.
"""

from __future__ import annotations

from engineering_team.modes.fix_contracts import FixRecord, ReproRun
from engineering_team.pipeline.state import PipelineState

MAX_HYPOTHESES = 3


def _run_line(label: str, run: ReproRun | None, missing: str) -> str:
    if run is None:
        return f"- {label}: {missing}"
    exit_code = "" if run.exit_code is None else f", exit {run.exit_code}"
    return f"- {label}: `{run.command}` {run.status}{exit_code} ({run.summary})"


def _reproduction(record: FixRecord) -> list[str]:
    if not record.reproduced:
        why = "; ".join(record.notes[-2:]) or "no attempt made a reproduction that failed"
        return [
            "- Reproduced: **no.** The team could not make a failing reproduction "
            f"({why[:300]}) and fixed the bug without one because `--allow-unreproduced` was "
            "given. Nothing here proves the bug is gone: try the steps from your report.",
        ]
    return [
        f"- Reproduced: **yes**, on attempt {record.attempts}.",
        _run_line("Red (before the fix)", record.red, "not recorded"),
        _run_line("Green (after the fix)", record.green, "NOT green: see Verification below"),
        f"- Regression test kept in the project: {', '.join(f'`{f}`' for f in record.files)}",
    ]


def render_fix_section(state: PipelineState) -> list[str]:
    """Lines (a heading first) for a fix run; empty for any other run."""

    record = state.fix
    if record is None:
        return []
    lines = ["## The fix", "", *_reproduction(record)]
    if record.user_repro:
        lines.append(_run_line("Your command", record.user_red, "it did not fail before the fix"))
    note, triage = state.fix_note, state.triage
    if note is not None:
        lines += ["", "### Root cause (the debugger's account, not evidence)", "", note.root_cause]
        if note.change:
            lines += ["", f"What changed: {note.change}"]
        lines += ["", "### Risk", "", note.risk or "The debugger named no risk."]
    if triage is not None and triage.hypotheses:
        lines += ["", "### Hypotheses from triage", ""]
        for item in triage.hypotheses[:MAX_HYPOTHESES]:
            where = f" ({', '.join(item.suspects)})" if item.suspects else ""
            lines.append(f"- [{item.likelihood}] {item.summary}{where}")
    return lines
