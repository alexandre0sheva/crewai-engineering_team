"""Settling a triaged request's questions before anything is planned.

A profile that ``needs_clarification`` has at most three questions. With a person available
(``--interactive`` in the terminal, the web UI) they are asked through the run's
:class:`~engineering_team.runtime.interaction.HumanChannel`; answers become ``clarifications`` and
unanswered questions become recorded assumptions. With no one to ask, a profile the model was
unsure of (``confidence: low``) stops the run the way fix mode does when it cannot reproduce a
bug: :class:`~engineering_team.modes.needs_info.NeedsInfo` (exit 4, ``needs-info``), nothing
changed. Otherwise the questions are recorded as assumptions and the run goes on.
"""

from __future__ import annotations

import threading

from engineering_team.contracts import TaskProfile
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.runtime.events import EventSink, NullSink
from engineering_team.runtime.interaction import HumanChannel

ASKER = "triage"


def settle(
    profile: TaskProfile,
    human: HumanChannel,
    *,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
    events: EventSink | None = None,
) -> TaskProfile:
    """``profile`` with its questions answered or assumed (``needs_clarification`` cleared).
    Raises :class:`NeedsInfo` when no one can answer and the profile's confidence is low."""

    if not profile.needs_clarification:
        return profile
    sink = events or NullSink()
    questions = list(profile.questions)
    if not human.interactive:
        if profile.confidence == "low":
            sink.emit("triage.needs_info", questions=questions)
            raise NeedsInfo(
                "The request is too unclear to start without answers, and no one can be asked "
                "(run with --interactive or from the web UI).",
                questions,
            )
        sink.emit("triage.assumed", questions=len(questions))
        return _assume(profile, questions, "no one to ask")
    answered: list[str] = []
    remaining: list[str] = []
    for question in questions:
        answer = human.ask(question, agent=ASKER, timeout=timeout, cancel_event=cancel_event)
        if answer and answer.strip():
            answered.append(f"Q: {question} A: {' '.join(answer.split())}")
        else:
            remaining.append(question)
    sink.emit("triage.clarified", asked=len(questions), answered=len(answered))
    settled = profile.model_copy(update={"clarifications": [*profile.clarifications, *answered]})
    return _assume(settled, remaining, "no answer")


def _assume(profile: TaskProfile, questions: list[str], why: str) -> TaskProfile:
    assumptions = list(profile.assumptions)
    for question in questions:
        assumptions.append(f"Not confirmed ({why}); the team goes on its assumption: {question}")
    return profile.model_copy(update={"assumptions": assumptions, "needs_clarification": False})
