"""The report's "Prompt sizes" section: what each stage session started with (always-on text,
the controller's pack, the task, the tool schemas) and the most history it carried, against the
model's context window (``runtime/prompt_sizes.py``); a session above the warning share of the
window is a warning."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from engineering_team.report.html_common import badge, esc, section, table
from engineering_team.report.model import Notice

HEAD = [
    "Stage",
    "Teammate",
    "Always-on",
    "Pack",
    "Task",
    "Tools",
    "Start",
    "History",
    "Window",
    "%",
]
MdTable = Callable[[Sequence[str], Sequence[Sequence[object]]], list[str]]
NOTE = (
    "Estimated tokens (four characters each). Start = always-on + pack + task + tools, what the "
    "first model call carries; History = the most conversation and tool output any call carried. "
    "The pack is the controller's hand-off, whose repository section stays within "
    "`context.stage_tokens`."
)


def _label(row: Mapping[str, Any]) -> str:
    lane = f" #{row['lane']}" if row.get("lane") is not None else ""
    return f"{row.get('stage') or '-'}{lane}"


def _cells(row: Mapping[str, Any], text: Callable[[object], str]) -> list[str]:
    window = int(row.get("window") or 0)
    return [
        text(_label(row)),
        text(row.get("agent") or "-"),
        f"{int(row.get('always_on') or 0):,}",
        f"{int(row.get('pack') or 0):,}",
        f"{int(row.get('task') or 0):,}",
        f"{int(row.get('tools') or 0):,}",
        f"{int(row.get('start') or 0):,}",
        f"{int(row.get('history') or 0):,}",
        f"{window:,}" if window else "unknown",
        f"{float(row.get('fraction') or 0):.0%}" if window else "-",
    ]


def notices(rows: Sequence[Mapping[str, Any]]) -> list[Notice]:
    """One warning for each session that started above the share of the window."""

    return [
        Notice(
            "warn",
            f"Stage {_label(row)} ({row.get('agent')}) started at {float(row['fraction']):.0%} of "
            f"{row.get('model') or 'the model'}'s {int(row['window']):,}-token context window "
            f"({int(row['start']):,} tokens); the history comes on top. Lower "
            "context.stage_tokens, narrow the request, or use a model with a larger window.",
        )
        for row in rows
        if row.get("warned")
    ]


def markdown(
    rows: Sequence[Mapping[str, Any]], cell: Callable[[object], str], md_table: MdTable
) -> list[str]:
    """The section as Markdown lines (``cell`` and ``md_table`` are ``render_md``'s)."""

    if not rows:
        return []
    return [
        "",
        "## Prompt sizes",
        "",
        NOTE.replace("`", ""),
        "",
        *md_table(HEAD, [_cells(row, cell) for row in rows]),
    ]


def html(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    body = [[*_cells(row, esc)[:-1], _percent(row)] for row in rows]
    return section(
        "prompts",
        "Prompt sizes",
        f"<p>{esc(NOTE.replace('`', ''))}</p>" + table(HEAD, body, tuple(range(2, 10))),
    )


def _percent(row: Mapping[str, Any]) -> str:
    text = _cells(row, esc)[-1]
    return badge(text, "warn") if row.get("warned") else text
