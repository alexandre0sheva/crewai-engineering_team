"""The task board's data model: cards, their comments and history, and progress figures.

Cards are :class:`~engineering_team.contracts.Contract` models like the rest of the run's state, so
``board.json`` is versioned and tolerant of fields a newer version adds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.contracts import Contract, utc_now

CardKind = Literal["stage", "work_package", "subtask", "repair", "finding", "check", "user_note"]
CardStatus = Literal[
    "backlog", "ready", "in_progress", "verifying", "blocked", "done", "failed", "cancelled"
]

# The board's columns, left to right. ``cancelled`` cards are kept (for the record) but shown apart.
COLUMNS: tuple[CardStatus, ...] = (
    "backlog",
    "ready",
    "in_progress",
    "verifying",
    "blocked",
    "done",
    "failed",
)
TERMINAL_STATUSES: frozenset[CardStatus] = frozenset({"done", "failed", "cancelled"})

# Who acted. Agents are identified by their teammate key; these two are reserved.
CONTROLLER = "controller"
USER = "user"


class _Part(BaseModel):
    """A value nested in a card: tolerant of unknown fields, without a version of its own."""

    model_config = ConfigDict(extra="ignore")


class Comment(_Part):
    ts: datetime = Field(default_factory=utc_now)
    author: str
    text: str
    # Recipients that already received this comment as steering (see ``take_steering``).
    delivered_to: list[str] = Field(default_factory=list)


class Move(_Part):
    """One entry of a card's history; the first has no ``from_status`` (the card was created)."""

    ts: datetime = Field(default_factory=utc_now)
    actor: str
    from_status: CardStatus | None = None
    to_status: CardStatus
    note: str = ""
    evidence: list[str] = Field(default_factory=list)  # check ids that justify the move


class Card(Contract):
    id: str
    title: str
    description: str = ""
    kind: CardKind
    status: CardStatus = "backlog"
    assignee: str | None = None  # teammate key
    lane: int | str | None = None
    stage: str | None = None
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    criteria_ids: list[str] = Field(default_factory=list)
    owned_paths: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    attempts: int = 0
    blocked_reason: str | None = None
    progress_note: str | None = None  # the assignee's latest one-line status
    comments: list[Comment] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # ids of the checks that justify ``done``
    tokens: int = 0
    cost_usd: float | None = None  # None: unknown
    created: datetime = Field(default_factory=utc_now)
    started: datetime | None = None
    finished: datetime | None = None
    history: list[Move] = Field(default_factory=list)


class BoardState(Contract):
    """Everything ``board.json`` holds."""

    run_id: str = ""
    paused: bool = False
    counter: int = 0  # the number of the last card id handed out
    updated: datetime | None = None
    cards: list[Card] = Field(default_factory=list)


class BlockedCard(_Part):
    id: str
    title: str
    assignee: str | None = None
    reason: str = ""


class OldestInProgress(_Part):
    id: str
    title: str
    age_seconds: float


class BoardProgress(Contract):
    """Where the run stands. Percentages are by card weight, not time: there is no ETA."""

    overall_percent: float = 0.0
    by_stage: dict[str, float] = Field(default_factory=dict)
    by_column: dict[str, int] = Field(default_factory=dict)
    cards_done: int = 0
    cards_total: int = 0
    blocked: list[BlockedCard] = Field(default_factory=list)
    oldest_in_progress: OldestInProgress | None = None
