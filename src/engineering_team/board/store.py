"""The task board store: cards, enforced transitions, steering, and the files that mirror them.

One :class:`BoardStore` per run. Every mutation happens under one lock, is written to
``board.json`` atomically (so the file alone is a complete record: each card carries its
history), is announced as a ``board.*`` event in the same order, and schedules a debounced
rewrite of ``board.md``. Readers get copies, never the live cards.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json, atomic_write_text
from engineering_team.board.models import (
    CONTROLLER,
    USER,
    BoardProgress,
    BoardState,
    Card,
    CardKind,
    CardStatus,
    Comment,
    Move,
)
from engineering_team.board.progress import compute_progress
from engineering_team.board.render import render_board
from engineering_team.board.rules import WIP_KINDS, BoardError, check_move, is_controller
from engineering_team.contracts import utc_now
from engineering_team.runtime.events import EventSink, NullSink

log = logging.getLogger(__name__)

BOARD_FILENAME = "board.json"
BOARD_MARKDOWN_FILENAME = "board.md"

MAX_CARDS = 500
MAX_COMMENTS_PER_CARD = 100
MAX_TITLE = 200
MAX_TEXT = 4000
MAX_REASON = 500
MAX_PROGRESS_NOTE = 200

# Fields the controller may change with ``update`` (everything else changes through a move).
UPDATABLE = frozenset(
    {
        "title",
        "description",
        "assignee",
        "lane",
        "stage",
        "depends_on",
        "criteria_ids",
        "owned_paths",
        "artifacts",
        "tokens",
        "cost_usd",
    }
)
_LIST_FIELDS = frozenset({"depends_on", "criteria_ids", "owned_paths", "artifacts"})


def _limit(name: str, value: str, maximum: int) -> str:
    value = value.strip()
    if not value:
        raise BoardError(f"{name} must not be empty.")
    if len(value) > maximum:
        raise BoardError(f"{name} is {len(value)} characters; the limit is {maximum}. Shorten it.")
    return value


class BoardStore:
    """The run's task board. Thread-safe; ``board.json`` is the machine format, ``board.md`` a view.

    ``max_in_progress`` is the WIP limit for the in-progress column (``max_parallel_agents``).
    ``render_delay`` debounces the Markdown rewrite (0 renders on every change).
    """

    def __init__(
        self,
        run_dir: Path,
        events: EventSink | None = None,
        *,
        run_id: str = "",
        max_in_progress: int = 3,
        clock: Callable[[], datetime] = utc_now,
        render_delay: float = 0.5,
    ) -> None:
        self.path = run_dir / BOARD_FILENAME
        self.markdown_path = run_dir / BOARD_MARKDOWN_FILENAME
        self.max_in_progress = max_in_progress
        self._events = events or NullSink()
        self._clock = clock
        self._render_delay = render_delay
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._state = self._load(run_id)
        self._running = threading.Event()  # set while the run is not paused
        if not self._state.paused:
            self._running.set()

    # -- reading -----------------------------------------------------------------------

    def get(self, card_id: str) -> Card:
        with self._lock:
            return self._card(card_id).model_copy(deep=True)

    def cards(
        self,
        *,
        status: CardStatus | None = None,
        kind: CardKind | None = None,
        assignee: str | None = None,
        stage: str | None = None,
        parent_id: str | None = None,
    ) -> list[Card]:
        """Copies of the cards that match every given filter, in creation order."""

        with self._lock:
            return [
                card.model_copy(deep=True)
                for card in self._state.cards
                if (status is None or card.status == status)
                and (kind is None or card.kind == kind)
                and (assignee is None or card.assignee == assignee)
                and (stage is None or card.stage == stage)
                and (parent_id is None or card.parent_id == parent_id)
            ]

    def snapshot(self) -> BoardState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def progress(self) -> BoardProgress:
        with self._lock:
            return compute_progress(self._state.cards, self._clock())

    # -- creating cards ----------------------------------------------------------------

    def create_card(
        self,
        title: str,
        *,
        kind: CardKind,
        actor: str = CONTROLLER,
        status: CardStatus = "backlog",
        **fields: Any,
    ) -> Card:
        """Add a card (the controller's call; agents use :meth:`create_subtask`).

        ``fields`` are card fields such as ``assignee``, ``stage``, ``parent_id``, ``depends_on``.
        """

        if status not in ("backlog", "ready"):
            raise BoardError(f"A new card starts in backlog or ready, not {status}.")
        with self._lock:
            return self._create(title, kind, actor, status, fields)

    def create_subtask(
        self, parent_id: str, title: str, *, actor: str, description: str = ""
    ) -> Card:
        """An agent adds a subtask under one of its own cards; it starts ``ready`` for that agent.

        Without ``parent_id`` the subtask hangs directly off the board. It inherits the
        parent's stage and lane.
        """

        with self._lock:
            fields: dict[str, Any] = {"assignee": actor, "description": description}
            if parent_id:
                parent = self._card(parent_id)
                if parent.assignee != actor and not is_controller(actor):
                    raise BoardError(
                        f"{parent.id} is not yours ({actor}); add subtasks only under your own"
                        f" cards. Use Comment On Card to ask its owner."
                    )
                fields.update(parent_id=parent.id, stage=parent.stage, lane=parent.lane)
            return self._create(title, "subtask", actor, "ready", fields)

    def add_user_note(self, text: str) -> Card:
        """A run-level note from the human, delivered once to every agent's next prompt."""

        text = _limit("The note", text, MAX_TEXT)
        comment = Comment(ts=self._clock(), author=USER, text=text)
        with self._lock:
            return self._create(text[:MAX_TITLE], "user_note", USER, "done", {}, [comment])

    def _create(
        self,
        title: str,
        kind: CardKind,
        actor: str,
        status: CardStatus,
        fields: dict[str, Any],
        comments: Sequence[Comment] = (),
    ) -> Card:
        if len(self._state.cards) >= MAX_CARDS:
            raise BoardError(f"The board is full ({MAX_CARDS} cards); finish or cancel some.")
        unknown = sorted(set(fields) - UPDATABLE - {"parent_id", "description"})
        if unknown:
            raise BoardError(f"Unknown card field(s): {', '.join(unknown)}.")
        for reference in [fields.get("parent_id"), *fields.get("depends_on", [])]:
            if reference:
                self._card(reference)  # raises if it does not exist
        now = self._clock()
        self._state.counter += 1
        card = Card(
            id=f"K-{self._state.counter:03d}",
            title=_limit("The title", title, MAX_TITLE),
            kind=kind,
            status=status,
            created=now,
            finished=now if status == "done" else None,
            comments=list(comments),
            history=[Move(ts=now, actor=actor, from_status=None, to_status=status)],
            **fields,
        )
        self._state.cards.append(card)
        self._changed()
        self._emit(
            "card_created",
            card,
            actor=actor,
            kind=card.kind,
            title=card.title,
            status=card.status,
            parent_id=card.parent_id,
        )
        return card.model_copy(deep=True)

    # -- changing cards ----------------------------------------------------------------

    def move(
        self,
        card_id: str,
        to: CardStatus,
        *,
        actor: str,
        reason: str = "",
        evidence: Sequence[str] = (),
        stage_success: bool = False,
    ) -> Card:
        """Move a card, if ``actor`` may (see ``rules``); otherwise raise :class:`BoardError`.

        ``done`` needs ``evidence`` (check ids) or ``stage_success``; ``blocked`` and ``failed``
        need a reason. The controller sending a ``verifying`` card back counts a new attempt.
        """

        reason = reason.strip()
        if len(reason) > MAX_REASON:
            raise BoardError(f"The reason is {len(reason)} characters; the limit is {MAX_REASON}.")
        with self._lock:
            card = self._card(card_id)
            check_move(
                card, to, actor, reason=reason, evidence=evidence, stage_success=stage_success
            )
            sent_back = card.status == "verifying" and to == "in_progress"
            if to == "in_progress" and card.kind in WIP_KINDS and not sent_back:
                self._check_wip(card)
            before, now = card.status, self._clock()
            if before in ("failed", "cancelled"):  # reopened by a resume: a fresh start
                card.finished = None
            card.status = to
            if to == "in_progress":
                card.started = card.started or now
                card.blocked_reason = None
                if before != "blocked":  # a new attempt: first start, or sent back by a check
                    card.attempts += 1
            elif to == "blocked":
                card.blocked_reason = reason
            else:
                card.blocked_reason = None
            if to == "done":
                card.evidence.extend(e for e in evidence if e not in card.evidence)
            if to in ("done", "failed", "cancelled"):
                card.finished = now
            note = reason or ("stage succeeded" if stage_success else "")
            card.history.append(
                Move(
                    ts=now,
                    actor=actor,
                    from_status=before,
                    to_status=to,
                    note=note,
                    evidence=list(evidence),
                )
            )
            self._changed()
            self._emit(
                "card_moved",
                card,
                actor=actor,
                from_status=before,
                to_status=to,
                reason=note,
                evidence=list(evidence),
                attempts=card.attempts,
            )
            return card.model_copy(deep=True)

    def comment(self, card_id: str, text: str, *, author: str) -> Card:
        """Add a comment; comments authored by ``user`` become steering for the assignee."""

        text = _limit("The comment", text, MAX_TEXT)
        with self._lock:
            card = self._card(card_id)
            if len(card.comments) >= MAX_COMMENTS_PER_CARD:
                raise BoardError(f"{card.id} already has {MAX_COMMENTS_PER_CARD} comments.")
            card.comments.append(Comment(ts=self._clock(), author=author, text=text))
            self._changed()
            self._emit("card_commented", card, author=author, text=text[:300])
            return card.model_copy(deep=True)

    def report_progress(self, card_id: str, text: str, *, actor: str) -> Card:
        """Set the one-line status of a card the actor owns (shown in the activity feed)."""

        text = " ".join(_limit("The status", text, MAX_PROGRESS_NOTE).split())
        with self._lock:
            card = self._card(card_id)
            if not is_controller(actor) and card.assignee != actor:
                raise BoardError(f"{card.id} is not assigned to you ({actor}).")
            card.progress_note = text
            self._changed()
            self._emit("card_updated", card, actor=actor, fields=["progress_note"], progress=text)
            return card.model_copy(deep=True)

    def update(self, card_id: str, **changes: Any) -> Card:
        """The controller's way to change other card fields (assignee, artifacts, tokens, ...)."""

        unknown = sorted(set(changes) - UPDATABLE)
        if unknown:
            raise BoardError(
                f"Cannot update {', '.join(unknown)}; allowed: {', '.join(sorted(UPDATABLE))}."
            )
        with self._lock:
            card = self._card(card_id)
            for name, value in changes.items():
                if name == "title":
                    value = _limit("The title", value, MAX_TITLE)
                if name in _LIST_FIELDS:
                    value = list(value)
                setattr(card, name, value)
            self._changed()
            self._emit("card_updated", card, actor=CONTROLLER, fields=sorted(changes))
            return card.model_copy(deep=True)

    # -- steering ----------------------------------------------------------------------

    def take_steering(self, agent: str) -> str:
        """The human's notes ``agent`` has not seen yet, as prompt text; each is delivered once.

        That is every user comment on a card assigned to ``agent`` plus every run-level note,
        formatted like ``User note on K-004: ...``. Returns "" when there is nothing new.
        """

        lines: list[str] = []
        with self._lock:
            for card in self._state.cards:
                if card.kind != "user_note" and card.assignee != agent:
                    continue
                for comment in card.comments:
                    if comment.author == USER and agent not in comment.delivered_to:
                        comment.delivered_to.append(agent)
                        label = (
                            "User note" if card.kind == "user_note" else f"User note on {card.id}"
                        )
                        lines.append(f"{label}: {comment.text}")
            if lines:
                self._changed()
                self._events.emit("board.steering_delivered", agent=agent, notes=len(lines))
        return "\n".join(lines)

    def pause(self) -> None:
        """Ask every agent to stop at its next safe point (its next tool call)."""

        self._set_paused(True)

    def unpause(self) -> None:
        self._set_paused(False)

    @property
    def paused(self) -> bool:
        return self._state.paused

    def wait_while_paused(
        self, cancel_event: threading.Event | None = None, *, poll: float = 0.1
    ) -> None:
        """Block while the run is paused; return at once when it is not, or once cancelled."""

        while not self._running.wait(timeout=poll):
            if cancel_event is not None and cancel_event.is_set():
                return

    def _set_paused(self, paused: bool) -> None:
        with self._lock:
            if self._state.paused == paused:
                return
            self._state.paused = paused
            if paused:
                self._running.clear()
            else:
                self._running.set()
            self._changed()
            self._events.emit("board.paused" if paused else "board.unpaused")

    # -- files -------------------------------------------------------------------------

    def flush(self) -> None:
        """Write ``board.json`` and ``board.md`` now (the Markdown is otherwise debounced)."""

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._save()
            self._render()

    def markdown(self) -> str:
        with self._lock:
            return render_board(self._state, compute_progress(self._state.cards, self._clock()))

    # -- internals ---------------------------------------------------------------------

    def _card(self, card_id: str) -> Card:
        wanted = card_id.strip().upper()
        for card in self._state.cards:
            if card.id == wanted:
                return card
        known = ", ".join(card.id for card in self._state.cards[:8]) or "none yet"
        raise BoardError(f"No card {card_id!r}. Use List Board Cards to see ids (e.g. {known}).")

    def _check_wip(self, moving: Card) -> None:
        busy = [
            card.id
            for card in self._state.cards
            if card.status == "in_progress" and card.kind in WIP_KINDS and card is not moving
        ]
        if len(busy) >= self.max_in_progress:
            raise BoardError(
                f"WIP limit reached: {len(busy)} cards are in progress "
                f"(limit {self.max_in_progress}: {', '.join(busy)}). "
                f"Finish or block one of them first; {moving.id} stays {moving.status}."
            )

    def _emit(self, name: str, card: Card, **data: Any) -> None:
        self._events.emit(
            f"board.{name}",
            card_id=card.id,
            agent=card.assignee,
            lane=card.lane,
            stage=card.stage,
            **data,
        )

    def _changed(self) -> None:
        self._state.updated = self._clock()
        self._save()
        if self._render_delay <= 0:
            self._render()
        elif self._timer is None:
            self._timer = threading.Timer(self._render_delay, self._render_later)
            self._timer.daemon = True
            self._timer.start()

    def _render_later(self) -> None:
        with self._lock:
            self._timer = None
            self._render()

    def _save(self) -> None:
        try:
            atomic_write_json(self.path, self._state.model_dump(mode="json"))
        except OSError:
            log.warning("Could not write %s", self.path, exc_info=True)

    def _render(self) -> None:
        try:
            atomic_write_text(self.markdown_path, self.markdown())
        except OSError:
            log.warning("Could not write %s", self.markdown_path, exc_info=True)

    def _load(self, run_id: str) -> BoardState:
        try:
            return BoardState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BoardState(run_id=run_id)
        except (OSError, ValueError):
            log.warning("Ignoring unreadable %s; starting an empty board", self.path, exc_info=True)
            return BoardState(run_id=run_id)
