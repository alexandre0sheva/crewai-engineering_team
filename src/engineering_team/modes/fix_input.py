"""What the person gave ``fix`` besides the bug report: a trace, a repro command, a permission.

The report, the trace excerpt, and the command go into the request text (so the agents read them
and ``request.md`` records them); the same facts are also kept as structured data in the run
directory, where the controller reads them (the agents cannot reach it). The file is written once,
when the run is opened, and read again by a resumed run.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.atomic_io import atomic_write_json
from engineering_team.modes.trace import parse_trace, suspect_files

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

INPUT_FILE = "fix-input.json"
MAX_TRACE_CHARS = 200_000  # what is kept for the controller
TRACE_EXCERPT = 6000  # what goes into the request text: the end of the log, where the error is
NO_REPORT = "Fix the bug described by the material below."


@dataclass(frozen=True)
class FixInput:
    repro: str | None = None  # the command the person says shows the bug (--repro)
    trace: str | None = None  # the stack trace or log (--trace-file)
    allow_unreproduced: bool = False  # fix even when no failing reproduction could be made


def write_fix_input(run_dir: Path, value: FixInput) -> None:
    data = asdict(value)
    data["trace"] = (value.trace or "")[-MAX_TRACE_CHARS:] or None
    atomic_write_json(run_dir / INPUT_FILE, data)


def read_fix_input(run_dir: Path) -> FixInput:
    """The run's inputs; the defaults when there is no file (a run that did not record any)."""

    try:
        data = json.loads((run_dir / INPUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FixInput()
    if not isinstance(data, dict):
        return FixInput()
    return FixInput(
        repro=data.get("repro") or None,
        trace=data.get("trace") or None,
        allow_unreproduced=bool(data.get("allow_unreproduced")),
    )


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    mark = "`" * max(3, longest + 1)
    return f"{mark}text\n{text.rstrip()}\n{mark}"


def compose_request(report: str | None, *, trace: str | None, repro: str | None) -> str:
    """The request text for a bug: the report (or a stand-in), then what else was given."""

    parts = [report.strip() if report and report.strip() else NO_REPORT]
    if trace and trace.strip():
        excerpt = trace.strip()[-TRACE_EXCERPT:]
        cut = " (the end of the file)" if len(trace.strip()) > TRACE_EXCERPT else ""
        parts.append(f"## Stack trace or log{cut}\n\n{_fence(excerpt)}")
    if repro and repro.strip():
        parts.append(
            "## Reproduction command from the user\n\n"
            f"{_fence(repro.strip())}\n\nThe person says this command shows the bug."
        )
    return "\n\n".join(parts)


def bug_brief(run_dir: Path, root: Path, state: PipelineState) -> str:
    """What the controller knows about the bug, as text for a prompt (empty when nothing).
    ``root`` is the workspace (the suspects are files found in it)."""

    record = state.fix
    given = read_fix_input(run_dir)
    lines: list[str] = []
    trace = record.trace if record is not None else parse_trace(given.trace or "")
    if trace is not None:
        suspects = record.suspects if record is not None else suspect_files(trace, root)
        lines += [
            "Stack trace the controller parsed (a fact about the report, not about the cause):",
            trace.render(suspects),
        ]
    repro = record.user_repro if record is not None else given.repro
    if repro:
        seen = ""
        if record is not None and record.user_red is not None:
            seen = f" The controller ran it: it fails ({record.user_red.summary})."
        elif record is not None:
            seen = " The controller ran it and it did NOT fail on the project as it is."
        lines.append(f"The person's reproduction command: `{repro}`.{seen}")
    if state.fix is not None and state.fix.reproduced:
        lines.append(
            f"The controller reproduced the bug itself: `{state.fix.command}` fails before the "
            f"fix ({state.fix.red.summary if state.fix.red else 'exit non-zero'}). It must "
            "pass after it. Files of the reproduction (do not change them): "
            + ", ".join(state.fix.files)
        )
    return "\n".join(lines)
