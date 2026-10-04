"""What the API says about a run: its summary, board (now or replayed), teammates, and cards.

Everything is derived from the run directory and its event log; nothing here asks a process.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from engineering_team.board.models import BoardProgress, BoardState, Card
from engineering_team.board.progress import compute_progress
from engineering_team.contracts import Event, RunManifest, UsageReport, utc_now
from engineering_team.runtime.run_index import RunRef
from engineering_team.ui.eventlog import EventLog, OpenQuestion, ToolRecord

BOARD_FILENAME = "board.json"
TRAIL_LIMIT = 200
ACTIVE_SECONDS = 30.0  # a teammate that used a tool this recently is "working"


class QuestionView(BaseModel):
    id: str
    text: str
    agent: str | None = None
    card_id: str | None = None
    asked: datetime


class RunView(BaseModel):
    """One run: the manifest as recorded, plus what is live about it."""

    run_id: str
    project: str
    workspace: str
    status: str  # a manifest status, or ``starting`` before the manifest exists
    manifest: RunManifest | None = None
    paused: bool = False
    cancel_requested: bool = False
    progress: BoardProgress | None = None
    usage: UsageReport | None = None
    questions: list[QuestionView] = Field(default_factory=list)
    last_seq: int = 0
    process: dict[str, Any] | None = None  # the process the UI started for it, if it did
    error: str = ""  # why a run that never started (or died) did not run


class BoardView(BaseModel):
    run_id: str
    seq: int  # the last event this board includes (replay: ``at``, clamped)
    at: datetime | None = None  # the time of that event
    paused: bool = False
    progress: BoardProgress
    cards: list[Card]


class AgentView(BaseModel):
    agent: str
    state: str  # working | waiting | idle
    card_id: str | None = None
    card_title: str | None = None
    cards: list[str] = Field(default_factory=list)  # every card assigned to it
    last_tool: str | None = None
    last_tool_at: datetime | None = None
    last_tool_ok: bool | None = None
    tool_calls: int = 0
    failed_calls: int = 0
    waiting_for: str | None = None  # the id of a question it asked


class TrailEntry(BaseModel):
    seq: int
    ts: datetime
    tool: str
    ok: bool
    duration: float
    lane: str | None = None
    args: str = ""


class CardDetail(BaseModel):
    card: Card
    trail: list[TrailEntry]
    trail_note: str = (
        "Tool calls by the card's assignee while the card was in progress (in its lane, when it "
        "has one); events do not name the card."
    )
    events: list[dict[str, Any]] = Field(default_factory=list)  # board events of this card


def load_board(run_dir: Path) -> BoardState:
    try:
        return BoardState.model_validate_json((run_dir / BOARD_FILENAME).read_text("utf-8"))
    except (OSError, ValueError):
        return BoardState()


def question_view(question: OpenQuestion) -> QuestionView:
    return QuestionView(
        id=question.id,
        text=question.text,
        agent=question.agent,
        card_id=question.card_id,
        asked=question.asked,
    )


def run_view(ref: RunRef, log: EventLog, process: dict[str, Any] | None = None) -> RunView:
    board = load_board(ref.run_dir)
    digest = log.digest()
    now = digest.last_ts or utc_now()
    return RunView(
        run_id=ref.run_id,
        project=ref.project,
        workspace=str(ref.workspace),
        status=ref.manifest.status,
        manifest=ref.manifest,
        paused=board.paused,
        cancel_requested=digest.cancel_requested,
        progress=compute_progress(board.cards, now) if board.cards else None,
        usage=log.usage(),
        questions=[question_view(q) for q in digest.questions.values()],
        last_seq=digest.last_seq,
        process=process,
    )


# -- the board, now and replayed ---------------------------------------------------------------


def board_now(ref: RunRef, log: EventLog) -> BoardView:
    board = load_board(ref.run_dir)
    digest = log.digest()
    now = utc_now() if not digest.finished else digest.last_ts or utc_now()
    return BoardView(
        run_id=ref.run_id,
        seq=digest.last_seq,
        at=digest.last_ts,
        paused=board.paused,
        progress=compute_progress(board.cards, now),
        cards=board.cards,
    )


def board_at(ref: RunRef, log: EventLog, seq: int) -> BoardView:
    """The board as it stood after event ``seq``, folded from the cards the ``board.*`` events
    carry. Deterministic: the same ``seq`` always gives the same board (progress is measured at
    the time of that event, not "now")."""

    cards: dict[str, Card] = {}
    paused = False
    last: Event | None = None
    for event in log.events_upto(seq):
        last = event
        if event.type == "board.paused":
            paused = True
        elif event.type == "board.unpaused":
            paused = False
        elif isinstance(event.data.get("card"), dict):
            card = Card.model_validate(event.data["card"])
            cards[card.id] = card
    ordered = sorted(cards.values(), key=lambda card: card.id)
    moment = last.ts if last else ref.manifest.created
    return BoardView(
        run_id=ref.run_id,
        seq=last.seq if last else 0,
        at=last.ts if last else None,
        paused=paused,
        progress=compute_progress(ordered, moment),
        cards=ordered,
    )


# -- teammates and cards -----------------------------------------------------------------------


def agents_view(ref: RunRef, log: EventLog) -> list[AgentView]:
    board = load_board(ref.run_dir)
    digest = log.digest()
    running = ref.manifest.status == "running" and not digest.finished
    now = digest.last_ts or utc_now()
    waiting = {q.agent: q.id for q in digest.questions.values() if q.agent}
    names = {c.assignee for c in board.cards if c.assignee and c.kind != "user_note"}
    names |= set(digest.agents) | set(waiting)
    views: list[AgentView] = []
    for name in sorted(names):
        mine = [c for c in board.cards if c.assignee == name and c.kind != "user_note"]
        current = next((c for c in mine if c.status in ("in_progress", "verifying")), None)
        stats = digest.agents.get(name)
        last = stats.last if stats else None
        recent = last is not None and (now - last.ts).total_seconds() <= ACTIVE_SECONDS
        state = "idle"
        if running and name in waiting:
            state = "waiting"
        elif running and (current is not None or recent):
            state = "working"
        views.append(
            AgentView(
                agent=name,
                state=state,
                card_id=current.id if current else None,
                card_title=current.title if current else None,
                cards=[c.id for c in mine],
                last_tool=last.tool if last else None,
                last_tool_at=last.ts if last else None,
                last_tool_ok=last.ok if last else None,
                tool_calls=stats.calls if stats else 0,
                failed_calls=stats.failed if stats else 0,
                waiting_for=waiting.get(name),
            )
        )
    return views


def _active_window(card: Card, now: datetime) -> tuple[datetime, datetime]:
    """From the card's first start to its last end: the time its assignee worked on it."""

    starts = [m.ts for m in card.history if m.to_status == "in_progress"]
    begin = card.started or (min(starts) if starts else card.created)
    return begin, card.finished or now


def card_detail(ref: RunRef, log: EventLog, card_id: str) -> CardDetail | None:
    board = load_board(ref.run_dir)
    wanted = card_id.strip().upper()
    card = next((c for c in board.cards if c.id == wanted), None)
    if card is None:
        return None
    digest = log.digest()
    begin, end = _active_window(card, digest.last_ts or utc_now())
    lane = None if card.lane is None else str(card.lane)
    trail: list[ToolRecord] = [
        record
        for record in digest.trail
        if card.assignee
        and record.agent == card.assignee
        and begin <= record.ts <= end
        and (lane is None or record.lane in (None, lane))
    ]
    related = [
        event.model_dump(mode="json", exclude={"run_id", "schema_version"})
        for event in _card_events(log, card.id)
    ]
    return CardDetail(
        card=card,
        trail=[
            TrailEntry(
                seq=r.seq,
                ts=r.ts,
                tool=r.tool,
                ok=r.ok,
                duration=r.duration,
                lane=r.lane,
                args=r.args,
            )
            for r in trail[-TRAIL_LIMIT:]
        ],
        events=related[-TRAIL_LIMIT:],
    )


def _card_events(log: EventLog, card_id: str) -> list[Event]:
    found = [
        event
        for event in log.events_upto(log.digest().last_seq)
        if event.type.startswith("board.") and event.data.get("card_id") == card_id
    ]
    for event in found:  # the card is shown once, on its own
        event.data.pop("card", None)
    return found
