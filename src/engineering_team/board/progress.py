"""Progress figures for a board: weighted percentages, counts, blockers. No ETA claims."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from engineering_team.board.models import (
    COLUMNS,
    BlockedCard,
    BoardProgress,
    Card,
    CardKind,
    OldestInProgress,
)

# Stages are one unit, a work package two, a check one, a subtask half. Repairs, findings and
# user notes appear mid-run and carry no weight, so a late discovery never makes progress go back.
WEIGHTS: dict[CardKind, float] = {
    "stage": 1.0,
    "work_package": 2.0,
    "check": 1.0,
    "subtask": 0.5,
}


def _percent(cards: Iterable[Card]) -> float:
    """Done weight over total weight; cancelled cards no longer count as planned work."""

    total = done = 0.0
    for card in cards:
        weight = WEIGHTS.get(card.kind, 0.0)
        if card.status == "cancelled" or not weight:
            continue
        total += weight
        if card.status == "done":
            done += weight
    return round(100.0 * done / total, 1) if total else 0.0


def compute_progress(cards: list[Card], now: datetime) -> BoardProgress:
    """Overall and per-stage percentages, cards per column, blockers, and the oldest card in flight.

    A stage's percentage is that of the other cards in it; a done stage card means 100. Cards of
    kind ``user_note`` are not board work and are left out of every figure.
    """

    work = [card for card in cards if card.kind != "user_note"]
    by_column: dict[str, int] = {status: 0 for status in COLUMNS}
    for card in work:
        if card.status != "cancelled":
            by_column[card.status] += 1
    by_stage: dict[str, float] = {}
    for name in dict.fromkeys(card.stage for card in work if card.stage):
        in_stage = [card for card in work if card.stage == name]
        finished = any(card.kind == "stage" and card.status == "done" for card in in_stage)
        by_stage[name] = 100.0 if finished else _percent(c for c in in_stage if c.kind != "stage")
    blocked = [
        BlockedCard(
            id=card.id, title=card.title, assignee=card.assignee, reason=card.blocked_reason or ""
        )
        for card in work
        if card.status == "blocked"
    ]
    running = sorted(
        ((card.started, card) for card in work if card.status == "in_progress" and card.started),
        key=lambda pair: pair[0],
    )
    oldest = None
    if running:
        started, card = running[0]
        oldest = OldestInProgress(
            id=card.id, title=card.title, age_seconds=round((now - started).total_seconds(), 1)
        )
    return BoardProgress(
        overall_percent=_percent(work),
        by_stage=by_stage,
        by_column=by_column,
        cards_done=by_column["done"],
        cards_total=sum(1 for card in work if card.status != "cancelled"),
        blocked=blocked,
        oldest_in_progress=oldest,
    )
