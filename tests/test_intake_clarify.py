"""Settling a triaged request's questions: ask a person, record assumptions, or stop needs-info."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from engineering_team.contracts import TaskProfile
from engineering_team.intake.clarify import settle
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.runtime.events import EventSink
from engineering_team.runtime.interaction import HumanChannel

QUESTIONS = ["Which export format?", "Sorted by first or last name?"]


class Recorder(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, type: str, **data: object) -> None:  # noqa: A002
        self.events.append((type, data))


def profile(**fields: object) -> TaskProfile:
    values: dict[str, object] = {
        "intents": ["new_project"],
        "needs_clarification": True,
        "questions": QUESTIONS,
        "assumptions": ["CSV export"],
        "confidence": "medium",
    }
    values.update(fields)
    return TaskProfile.model_validate(values)


@pytest.fixture
def answering() -> Iterator[tuple[HumanChannel, dict[str, str | None]]]:
    """An interactive channel whose questions a thread answers from ``replies`` (``None``
    skips the question)."""

    human = HumanChannel(interactive=True)
    replies: dict[str, str | None] = {}
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(0.01):
            for question in human.pending():
                reply = replies.get(question.text)
                if reply is None:
                    human.skip(question.id)
                else:
                    human.answer(question.id, reply)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    yield human, replies
    stop.set()
    thread.join(timeout=1)


def test_a_clear_profile_is_left_alone() -> None:
    clear = profile(needs_clarification=False)

    assert settle(clear, HumanChannel()) is clear


def test_with_no_one_to_ask_the_questions_become_assumptions() -> None:
    events = Recorder()

    settled = settle(profile(), HumanChannel(), events=events)

    assert not settled.needs_clarification
    assert settled.assumptions[0] == "CSV export"
    assert settled.assumptions[1:] == [
        f"Not confirmed (no one to ask); the team goes on its assumption: {q}" for q in QUESTIONS
    ]
    assert events.events == [("triage.assumed", {"questions": 2})]


def test_with_no_one_to_ask_a_low_confidence_profile_stops_needs_info() -> None:
    events = Recorder()

    with pytest.raises(NeedsInfo) as stopped:
        settle(profile(confidence="low"), HumanChannel(), events=events)

    assert stopped.value.questions == QUESTIONS
    assert events.events == [("triage.needs_info", {"questions": QUESTIONS})]


def test_a_person_answers_some_questions_and_the_rest_are_assumed(
    answering: tuple[HumanChannel, dict[str, str | None]],
) -> None:
    human, replies = answering
    replies[QUESTIONS[0]] = "  JSON,   please "
    events = Recorder()

    settled = settle(profile(confidence="low"), human, timeout=5, events=events)

    assert settled.clarifications == [f"Q: {QUESTIONS[0]} A: JSON, please"]
    assert settled.assumptions[-1].endswith(QUESTIONS[1])
    assert "no answer" in settled.assumptions[-1]
    assert not settled.needs_clarification
    assert events.events == [("triage.clarified", {"asked": 2, "answered": 1})]
