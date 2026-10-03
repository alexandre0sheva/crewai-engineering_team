"""The task board as a kanban: one column per status, or a compact list in a narrow terminal."""

from __future__ import annotations

from datetime import datetime

from rich import box
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from engineering_team.board.models import COLUMNS, Card

TITLES = {
    "backlog": "Backlog",
    "ready": "Ready",
    "in_progress": "In progress",
    "verifying": "Verifying",
    "blocked": "Blocked",
    "done": "Done",
    "failed": "Failed",
}
STYLES = {
    "backlog": "dim",
    "ready": "cyan",
    "in_progress": "yellow",
    "verifying": "magenta",
    "blocked": "red",
    "done": "green",
    "failed": "bold red",
}
MIN_COLUMN_WIDTH = 22
DONE_SHOWN = 6


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _age(card: Card, now: datetime) -> str:
    if card.status in ("done", "failed") and card.started and card.finished:
        return format_age((card.finished - card.started).total_seconds())
    if card.status in ("backlog", "ready"):
        return ""
    return format_age((now - (card.started or card.created)).total_seconds())


def _lines(card: Card, now: datetime) -> list[Text]:
    """A card's lines: ``K-004 title``, then who and how long (or why it is blocked)."""

    head = Text(overflow="fold")
    head.append(card.id, style="bold")
    head.append(f" {card.title}")
    details = [part for part in (card.assignee, _age(card, now)) if part]
    if card.lane is not None:
        details.append(f"lane {card.lane}")
    lines = [head]
    if details:
        lines.append(Text(" · ".join(details), style="dim", overflow="fold"))
    if card.status == "blocked" and card.blocked_reason:
        lines.append(Text(f"⛔ {card.blocked_reason}", style="red", overflow="fold"))
    return lines


def board_columns(cards: list[Card]) -> dict[str, list[Card]]:
    """Cards by status in column order; ``Failed`` appears only when something failed."""

    work = [card for card in cards if card.kind != "user_note" and card.status != "cancelled"]
    shown = [
        status
        for status in COLUMNS
        if status != "failed" or any(c.status == "failed" for c in work)
    ]
    return {
        status: sorted((c for c in work if c.status == status), key=lambda c: c.id)
        for status in shown
    }


def _done_window(cards: list[Card]) -> tuple[list[Card], int]:
    if len(cards) <= DONE_SHOWN:
        return cards, 0
    return cards[-DONE_SHOWN:], len(cards) - DONE_SHOWN


def render_kanban(cards: list[Card], *, width: int, now: datetime) -> RenderableType:
    """The board for a terminal ``width`` columns wide (deterministic for a given ``now``)."""

    columns = board_columns(cards)
    if not any(columns.values()):
        return Text("The board is empty (this run has no task board yet).", style="dim")
    if width >= MIN_COLUMN_WIDTH * len(columns):
        return _wide(columns, now)
    return _compact(columns, now)


def _cell(status: str, cards: list[Card], now: datetime) -> RenderableType:
    hidden = 0
    if status == "done":
        cards, hidden = _done_window(cards)
    parts: list[RenderableType] = []
    for card in cards:
        parts.extend(_lines(card, now))
        parts.append(Text(""))
    if hidden:
        parts.append(Text(f"… +{hidden} earlier", style="dim"))
    return Group(*parts) if parts else Text("")


def _wide(columns: dict[str, list[Card]], now: datetime) -> RenderableType:
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False, pad_edge=False)
    for status, cards in columns.items():
        table.add_column(
            f"{TITLES[status]} ({len(cards)})",
            style=STYLES[status],
            ratio=1,
        )
    table.add_row(*(_cell(status, cards, now) for status, cards in columns.items()))
    return table


def _compact(columns: dict[str, list[Card]], now: datetime) -> RenderableType:
    parts: list[RenderableType] = []
    for status, cards in columns.items():
        if not cards:
            continue
        parts.append(Text(f"{TITLES[status]} ({len(cards)})", style=f"bold {STYLES[status]}"))
        shown, hidden = _done_window(cards) if status == "done" else (cards, 0)
        for card in shown:
            lines = _lines(card, now)
            lines[0] = Text.assemble("  ", lines[0])
            parts.extend(lines[:1])
            parts.extend(Text.assemble("    ", line) for line in lines[1:])
        if hidden:
            parts.append(Text(f"  … +{hidden} earlier", style="dim"))
    return Group(*parts)
