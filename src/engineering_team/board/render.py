"""``board.md``: the board as Markdown, one section per column, for the run folder."""

from __future__ import annotations

from engineering_team.board.models import COLUMNS, BoardProgress, BoardState, Card

TITLES = {
    "backlog": "Backlog",
    "ready": "Ready",
    "in_progress": "In progress",
    "verifying": "Verifying",
    "blocked": "Blocked",
    "done": "Done",
    "failed": "Failed",
}


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _age(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


def _notes(card: Card) -> str:
    parts = []
    if card.blocked_reason:
        parts.append(f"blocked: {card.blocked_reason}")
    if card.progress_note:
        parts.append(card.progress_note)
    if card.attempts > 1:
        parts.append(f"attempt {card.attempts}")
    return _cell("; ".join(parts))


def _table(cards: list[Card]) -> list[str]:
    lines = [
        "| ID | Title | Kind | Assignee | Stage | Notes |",
        "|----|-------|------|----------|-------|-------|",
    ]
    for card in cards:
        lines.append(
            f"| {card.id} | {_cell(card.title)} | {card.kind} | {card.assignee or '-'} "
            f"| {card.stage or '-'} | {_notes(card)} |"
        )
    return lines


def render_board(state: BoardState, progress: BoardProgress) -> str:
    """The whole board: progress, blockers, then every column as a table."""

    work = [card for card in state.cards if card.kind != "user_note"]
    lines = ["# Task board", ""]
    summary = (
        f"{progress.overall_percent:g}% complete · {progress.cards_done} of "
        f"{progress.cards_total} cards done"
    )
    if state.paused:
        summary += " · PAUSED"
    lines += [f"Run `{state.run_id}` · {summary}" if state.run_id else summary, ""]
    if progress.oldest_in_progress:
        oldest = progress.oldest_in_progress
        lines += [
            f"Oldest in progress: {oldest.id} {_cell(oldest.title)} ({_age(oldest.age_seconds)})",
            "",
        ]
    if progress.by_stage:
        lines += [
            "Stages: " + " · ".join(f"{name} {pct:g}%" for name, pct in progress.by_stage.items()),
            "",
        ]
    if progress.blocked:
        lines += ["**Blocked**", ""]
        lines += [
            f"- {item.id} {_cell(item.title)} ({item.assignee or 'unassigned'}): "
            f"{_cell(item.reason)}"
            for item in progress.blocked
        ]
        lines.append("")
    for status in COLUMNS:
        cards = [card for card in work if card.status == status]
        lines += [f"## {TITLES[status]} ({len(cards)})", ""]
        lines += _table(cards) if cards else ["_none_"]
        lines.append("")
    cancelled = [card for card in work if card.status == "cancelled"]
    if cancelled:
        lines += [f"## Cancelled ({len(cancelled)})", "", *_table(cancelled), ""]
    notes = [card for card in state.cards if card.kind == "user_note"]
    if notes:
        lines += [f"## User notes ({len(notes)})", ""]
        lines += [
            f"- {card.id}: {_cell(card.comments[0].text if card.comments else card.title)}"
            for card in notes
        ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
