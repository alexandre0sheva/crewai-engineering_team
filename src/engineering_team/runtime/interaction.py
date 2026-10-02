"""Questions from agents to the human, and how answers come back.

An agent asks with :meth:`HumanChannel.ask`, which blocks until the question is answered, the
timeout passes, or the run is cancelled. Nothing is wired by default: the channel starts
non-interactive and ``ask`` returns ``None`` at once. A front end (the CLI prompt, the web UI)
calls :meth:`enable`, shows :meth:`pending` questions, and delivers each answer with
:meth:`answer`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from engineering_team.contracts import utc_now
from engineering_team.runtime.events import EventSink, NullSink


@dataclass
class Question:
    id: str
    text: str
    agent: str | None = None
    card_id: str | None = None
    asked: datetime = field(default_factory=utc_now)
    answer: str | None = None
    _answered: threading.Event = field(default_factory=threading.Event, repr=False)


class HumanChannel:
    """The run's line to the human. Thread-safe."""

    def __init__(self, events: EventSink | None = None, *, interactive: bool = False) -> None:
        self._events = events or NullSink()
        self._interactive = interactive
        self._lock = threading.Lock()
        self._questions: dict[str, Question] = {}
        self._counter = 0

    @property
    def interactive(self) -> bool:
        return self._interactive

    def enable(self, interactive: bool = True) -> None:
        """Declare that a front end will answer questions (or, with ``False``, that none will)."""

        self._interactive = interactive

    def pending(self) -> list[Question]:
        """Questions still waiting for an answer, oldest first."""

        with self._lock:
            return [q for q in self._questions.values() if q.answer is None]

    def answer(self, question_id: str, text: str) -> bool:
        """Deliver an answer; ``False`` if there is no such open question."""

        with self._lock:
            question = self._questions.get(question_id)
            if question is None or question.answer is not None:
                return False
            question.answer = text
        self._events.emit("question.answered", question_id=question.id, agent=question.agent)
        question._answered.set()
        return True

    def ask(
        self,
        text: str,
        *,
        agent: str | None = None,
        card_id: str | None = None,
        timeout: float = 300.0,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """Ask the human and wait. Returns the answer, or ``None`` when nobody answered
        (non-interactive run, timeout, or cancellation)."""

        with self._lock:
            self._counter += 1
            question = Question(f"Q-{self._counter:03d}", text, agent, card_id)
            self._questions[question.id] = question
        self._events.emit(
            "question",
            question_id=question.id,
            text=text,
            agent=agent,
            card_id=card_id,
            interactive=self._interactive,
        )
        if not self._interactive:
            self._close(question, "no_human")
            return None
        end = time.monotonic() + timeout
        outcome = "timeout"
        while question.answer is None:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            if cancel_event is not None and cancel_event.is_set():
                outcome = "cancelled"
                break
            question._answered.wait(timeout=min(0.1, remaining))
        if question.answer is None:
            self._close(question, outcome)
        return question.answer

    def _close(self, question: Question, outcome: str) -> None:
        with self._lock:
            if question.answer is not None:
                return
            self._questions.pop(question.id, None)
        self._events.emit(
            "question.unanswered", question_id=question.id, outcome=outcome, agent=question.agent
        )
