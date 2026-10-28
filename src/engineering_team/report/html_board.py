"""The report's task board and teammates sections.

Every string that did not come from this module goes through ``esc``.
"""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.board.models import COLUMNS, Card
from engineering_team.report.fmt import clock, time_of_day
from engineering_team.report.html_common import (
    COLUMN_TITLES,
    MAX_COMMENTS,
    badge,
    esc,
    section,
    table,
)
from engineering_team.report.model import (
    RunReport,
)

# -- board, teammates ------------------------------------------------------------------------


def _card(card: Card) -> str:
    facts = [card.kind, card.assignee or "unassigned"]
    if card.attempts > 1:
        facts.append(f"attempt {card.attempts}")
    if card.tokens:
        facts.append(f"{card.tokens:,} tokens")
    notes = ""
    if card.blocked_reason:
        notes += f"<div>Blocked: {esc(card.blocked_reason)}</div>"
    if card.progress_note:
        notes += f'<div class="muted">{esc(card.progress_note)} (agent-reported)</div>'
    return (
        f'<div class="card"><strong>{esc(card.id)}</strong> {esc(card.title)}'
        f'<div class="muted">{esc(" · ".join(facts))}</div>{notes}</div>'
    )


def _evidence(ids: Sequence[str]) -> str:
    return f" — evidence: {esc(', '.join(ids))}" if ids else ""


def _history(card: Card) -> str:
    moves = "".join(
        f"<tr><td>{esc(time_of_day(m.ts))}</td><td>{esc(m.actor)}</td>"
        f"<td>{esc(m.from_status or '(created)')} → {esc(m.to_status)}</td>"
        f"<td>{esc(m.note)}{_evidence(m.evidence)}</td></tr>"
        for m in card.history
    )
    comments = "".join(
        f"<li>{esc(c.author)}: {esc(c.text)}</li>" for c in card.comments[:MAX_COMMENTS]
    )
    more = len(card.comments) - MAX_COMMENTS
    extra = f"<li>… {more} more comment(s)</li>" if more > 0 else ""
    notes = f"<ul>{comments}{extra}</ul>" if comments else ""
    body = (
        "<table><thead><tr><th>Time (UTC)</th><th>Who</th><th>Move</th><th>Note</th></tr></thead>"
        f"<tbody>{moves}</tbody></table>{notes}"
    )
    label = f"{esc(card.id)} {esc(card.title)} · {len(card.history)} move(s) {badge(card.status)}"
    return f"<details><summary>{label}</summary>{body}</details>"


def board(report: RunReport) -> str:
    work = [c for c in report.cards if c.kind != "user_note"]
    if not work:
        return section(
            "board",
            "Task board",
            '<p class="muted">The board is empty (only the pipeline strategy fills it in).</p>',
        )
    progress = report.progress
    head = ""
    if progress is not None:
        head = (
            f"<p>{progress.overall_percent:g}% complete · {progress.cards_done} of "
            f"{progress.cards_total} cards done{' · PAUSED' if report.paused else ''}</p>"
        )
    columns = ""
    for status in (*COLUMNS, "cancelled"):
        cards = [c for c in work if c.status == status]
        if not cards and status == "cancelled":
            continue
        title = COLUMN_TITLES.get(status, status.capitalize())
        columns += (
            f'<div class="col"><h3>{esc(title)} ({len(cards)})</h3>'
            f"{''.join(_card(c) for c in cards) or '<span class=muted>none</span>'}</div>"
        )
    history = "".join(_history(c) for c in work)
    return section(
        "board",
        "Final task board",
        f'{head}<div class="columns">{columns}</div><h3>Card history</h3>{history}',
    )


def agents(report: RunReport) -> str:
    if not report.agents:
        return ""
    rows = [
        [
            esc(a.agent),
            str(a.tool_calls),
            str(a.failed_calls),
            esc(clock(a.seconds)),
            esc(", ".join(a.cards) or "-"),
            esc(", ".join(f"{name} ×{count}" for name, count in list(a.tools.items())[:5]) or "-"),
        ]
        for a in report.agents
    ]
    return section(
        "agents",
        "Teammates and tool calls",
        table(
            ["Teammate", "Tool calls", "Failed", "Tool time", "Cards", "Most used"], rows, (1, 2)
        ),
    )
