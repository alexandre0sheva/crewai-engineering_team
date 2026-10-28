"""Rendering a trajectory as text: the ``[ACTION]`` / ``[OBSERVATION]`` / ``[ERROR]`` /
``[MESSAGE]`` grammar that ``engineering-team trace`` prints and later tooling reads.

Entries are grouped by stage (stages in the order they first appear), and within a stage ordered
by time, because CrewAI delivers a model reply on a handler thread and so records it a moment
after the tool call it asked for. A line starting with ``[`` always begins a new item; the
continuation lines of a multi-line output are indented by two spaces.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from engineering_team.runtime.trajectory import TrajectoryEntry

NO_STAGE = "(no stage)"
MAX_ARGUMENT_CHARS = 200
ERROR_PREFIX = "ERROR:"


def stages_of(entries: Iterable[TrajectoryEntry]) -> list[str]:
    """Stage names in first-seen order (``NO_STAGE`` for entries recorded outside a stage)."""

    seen: dict[str, None] = {}
    for entry in entries:
        seen.setdefault(entry.stage or NO_STAGE, None)
    return list(seen)


def ordered(entries: Iterable[TrajectoryEntry], stage: str | None = None) -> list[TrajectoryEntry]:
    """The entries of ``stage`` (all when ``None``), stage by stage and by time within a stage."""

    found = list(entries)
    names = stages_of(found)
    wanted = [stage] if stage is not None else names
    result: list[TrajectoryEntry] = []
    for name in wanted:
        group = [entry for entry in found if (entry.stage or NO_STAGE) == name]
        result.extend(sorted(group, key=lambda entry: (entry.ts, entry.seq)))
    return result


def _indent(text: str) -> str:
    return text.replace("\n", "\n  ")


def _argument(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_ARGUMENT_CHARS else text[:MAX_ARGUMENT_CHARS] + "..."


def _who(entry: TrajectoryEntry) -> str:
    who = entry.teammate or "unknown"
    return who if entry.lane is None else f"{who}#{entry.lane}"


def render_entry(entry: TrajectoryEntry) -> list[str]:
    """The block of lines one entry renders as."""

    if entry.kind == "notice":
        return [f"[NOTICE] {_indent(entry.text)}"]
    if entry.kind == "message":
        label = _who(entry) + (" (final)" if entry.role == "final" else "")
        return [f"[MESSAGE] {label}: {_indent(entry.text.rstrip())}"]
    arguments = ", ".join(f"{name}={_argument(value)}" for name, value in entry.args.items())
    lines = [f"[ACTION] {entry.tool}({arguments})  [#{entry.seq} {_who(entry)}, {entry.ms} ms]"]
    output = entry.output.rstrip()
    if not entry.ok:
        output = output.removeprefix(ERROR_PREFIX).strip()
        lines.append(f"[ERROR] {_indent(output) or '(no message)'}")
    else:
        lines.append(f"[OBSERVATION] {_indent(output) or '(empty)'}")
    return lines


def render_text(entries: Sequence[TrajectoryEntry], *, stage: str | None = None) -> str:
    """The trace as text, with a header per stage."""

    lines: list[str] = []
    current: str | None = None
    for entry in ordered(entries, stage):
        name = entry.stage or NO_STAGE
        if name != current:
            current = name
            if lines:
                lines.append("")
            lines.append(f"== stage: {name} ==")
        lines.extend(render_entry(entry))
    return "\n".join(lines) + ("\n" if lines else "")


def render_json(
    run_id: str, entries: Sequence[TrajectoryEntry] | None, *, stage: str | None = None
) -> str:
    """The trace as JSON: ``recorded`` says whether the run has a trajectory at all."""

    payload = {
        "run_id": run_id,
        "recorded": entries is not None,
        "stage": stage,
        "entries": [
            entry.model_dump(mode="json", exclude_defaults=False)
            for entry in ordered(entries or [], stage)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
