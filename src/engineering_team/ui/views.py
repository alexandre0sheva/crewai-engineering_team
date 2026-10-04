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
from engineering_team.runtime.budget import WARN_FRACTION
from engineering_team.runtime.run_index import RunRef
from engineering_team.runtime.run_store import TERMINAL_STATUSES
from engineering_team.settings import BudgetSettings
from engineering_team.ui.eventlog import Digest, EventLog, OpenQuestion, ToolRecord

BOARD_FILENAME = "board.json"
TRAIL_LIMIT = 200
ACTIVE_SECONDS = 30.0  # a teammate that used a tool this recently is "working"


class QuestionView(BaseModel):
    id: str
    text: str
    agent: str | None = None
    card_id: str | None = None
    asked: datetime


class AttentionItem(BaseModel):
    """One thing a person may need to act on; the page lists these without interpreting them."""

    kind: str  # question | blocked | budget | check
    severity: str  # info | warn | bad
    text: str
    card_id: str | None = None
    question_id: str | None = None


class CheckView(BaseModel):
    id: str
    name: str
    kind: str
    status: str  # running | passed | failed | skipped | unavailable
    summary: str = ""
    duration: float | None = None


class BudgetLimitView(BaseModel):
    name: str
    used: float
    max: float
    fraction: float


class BudgetView(BaseModel):
    """The run against its limits, now (``unlimited`` when none is set)."""

    state: str  # unlimited | ok | warning | exceeded
    limits: list[BudgetLimitView] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    budget: BudgetView | None = None
    checks: list[CheckView] = Field(default_factory=list)
    attention: list[AttentionItem] = Field(default_factory=list)
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
    state: str  # working | waiting (for a human) | blocked | idle
    card_id: str | None = None
    card_title: str | None = None
    cards: list[str] = Field(default_factory=list)  # every card assigned to it
    last_tool: str | None = None
    last_tool_at: datetime | None = None
    last_tool_ok: bool | None = None
    model: str | None = None
    tokens: int = 0
    cost_usd: float | None = None
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


def run_view(
    ref: RunRef,
    log: EventLog,
    process: dict[str, Any] | None = None,
    limits: BudgetSettings | None = None,
) -> RunView:
    board = load_board(ref.run_dir)
    digest = log.digest()
    now = digest.last_ts or utc_now()
    usage = log.usage()
    progress = compute_progress(board.cards, now) if board.cards else None
    questions = [question_view(q) for q in digest.questions.values()]
    ended = ref.manifest.finished if ref.manifest.status in TERMINAL_STATUSES else None
    elapsed = ((ended or utc_now()) - ref.manifest.created).total_seconds()
    budget = budget_view(limits or BudgetSettings(), usage, elapsed, digest)
    checks = [
        CheckView(
            id=c.id, name=c.name, kind=c.kind, status=c.status, summary=c.summary,
            duration=c.duration,
        )
        for c in digest.checks.values()
    ]  # fmt: skip
    return RunView(
        run_id=ref.run_id,
        project=ref.project,
        workspace=str(ref.workspace),
        status=ref.manifest.status,
        manifest=ref.manifest,
        paused=board.paused,
        cancel_requested=digest.cancel_requested,
        progress=progress,
        usage=usage,
        questions=questions,
        budget=budget,
        checks=checks,
        attention=attention_items(progress, questions, budget, checks),
        last_seq=digest.last_seq,
        process=process,
    )


def budget_view(
    limits: BudgetSettings, usage: UsageReport, elapsed: float, digest: Digest
) -> BudgetView:
    """Usage against each configured limit (the same measures the run's own budget uses)."""

    used = {
        "max_cost_usd": usage.known_cost_usd,
        "max_tokens": float(usage.totals.total_tokens),
        "max_wall_seconds": elapsed,
        "max_tool_calls": float(usage.tool_calls),
    }
    found = [
        BudgetLimitView(
            name=name, used=used[name], max=float(maximum), fraction=used[name] / float(maximum)
        )
        for name in used
        if (maximum := getattr(limits, name)) is not None
    ]
    state = "unlimited" if not found else "ok"
    if any(limit.fraction >= WARN_FRACTION for limit in found):
        state = "warning"
    if any(limit.fraction > 1 for limit in found) or any(
        n.exceeded for n in digest.budget.values()
    ):
        state = "exceeded"
    notes = [n.note for n in digest.budget.values() if n.note]
    return BudgetView(state=state, limits=found, notes=notes)


def attention_items(
    progress: BoardProgress | None,
    questions: list[QuestionView],
    budget: BudgetView,
    checks: list[CheckView],
) -> list[AttentionItem]:
    """What needs a person, most urgent first: questions, blocked cards, budget, failed checks."""

    items = [
        AttentionItem(
            kind="question", severity="bad", text=f"{q.agent or 'The team'} asks: {q.text}",
            card_id=q.card_id, question_id=q.id,
        )
        for q in questions
    ]  # fmt: skip
    for card in progress.blocked if progress else []:
        reason = f": {card.reason}" if card.reason else ""
        items.append(
            AttentionItem(
                kind="blocked",
                severity="warn",
                text=f"{card.id} is blocked{reason}",
                card_id=card.id,
            )
        )
    if budget.state in ("warning", "exceeded"):
        hot = [
            f"{limit.name} {limit.fraction:.0%}"
            for limit in budget.limits
            if limit.fraction >= WARN_FRACTION
        ]
        text = "Budget " + budget.state + (f" ({', '.join(hot)})" if hot else "")
        items.append(
            AttentionItem(
                kind="budget", severity="bad" if budget.state == "exceeded" else "warn", text=text
            )
        )
    for note in budget.notes:
        items.append(AttentionItem(kind="budget", severity="warn", text=note))
    for check in checks:
        if check.status in ("failed", "unavailable"):
            detail = f": {check.summary}" if check.summary else ""
            items.append(
                AttentionItem(
                    kind="check",
                    severity="bad" if check.status == "failed" else "warn",
                    text=f"Check {check.name} {check.status}{detail}",
                )
            )
    return items


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


def agents_view(ref: RunRef, log: EventLog, at: int | None = None) -> list[AgentView]:
    """The teammates now, or as they stood after event ``at`` (the replay)."""

    if at is None:
        cards = load_board(ref.run_dir).cards
        digest, usage = log.digest(), log.usage()
        running = ref.manifest.status == "running" and not digest.finished
    else:
        cards = board_at(ref, log, at).cards
        digest, usage = log.digest_upto(at)
        running = not digest.finished
    now = digest.last_ts or utc_now()
    waiting = {q.agent: q.id for q in digest.questions.values() if q.agent}
    names = {c.assignee for c in cards if c.assignee and c.kind != "user_note"}
    names |= set(digest.agents) | set(waiting)
    models = {row.agent: row.model for row in usage.rows if row.agent}
    views: list[AgentView] = []
    for name in sorted(names):
        mine = [c for c in cards if c.assignee == name and c.kind != "user_note"]
        current = next((c for c in mine if c.status in ("in_progress", "verifying")), None)
        stuck = next((c for c in mine if c.status == "blocked"), None)
        stats = digest.agents.get(name)
        last = stats.last if stats else None
        recent = last is not None and (now - last.ts).total_seconds() <= ACTIVE_SECONDS
        state = "idle"
        if running and name in waiting:
            state = "waiting"
        elif running and (current is not None or recent):
            state = "working"
        elif running and stuck is not None:
            state = "blocked"
        by_agent = usage.by_agent.get(name)
        cost = sum(r.cost_usd or 0.0 for r in usage.rows if r.agent == name)
        priced = all(r.cost_usd is not None for r in usage.rows if r.agent == name)
        views.append(
            AgentView(
                agent=name,
                state=state,
                card_id=current.id if current else (stuck.id if stuck else None),
                card_title=current.title if current else (stuck.title if stuck else None),
                cards=[c.id for c in mine],
                last_tool=last.tool if last else None,
                last_tool_at=last.ts if last else None,
                last_tool_ok=last.ok if last else None,
                model=models.get(name),
                tokens=by_agent.total_tokens if by_agent else 0,
                cost_usd=round(cost, 4) if priced and by_agent else None,
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
