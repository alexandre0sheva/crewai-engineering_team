"""The ``spec`` stage's controller side: validating the analyst's ``Spec``, asking the person
what the analyst could not decide, and writing ``docs/spec.md``.

The analyst (an agent) only returns the contract. The controller checks the criterion ids,
settles the open questions (asking when a human is available, otherwise recording what was
assumed), and renders ``docs/spec.md`` itself, so the file always matches the contract the
later stages read.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from engineering_team.contracts import Spec
from engineering_team.pipeline.stages import StageError
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.context import RunContext

SPEC_FILE = "docs/spec.md"
ANALYST = "product_analyst"
CRITERION_ID = re.compile(r"AC-[1-9][0-9]*")
LOW_CONFIDENCE = (
    "The analyst was not confident in this reading of the request; review docs/spec.md "
    "before relying on the result."
)


class SpecError(StageError):
    """A specification the pipeline cannot build on; the stage is retried with this message."""


def spec_problems(spec: Spec) -> list[str]:
    """Why ``spec`` cannot be the contract for the later stages (empty when it can)."""

    problems: list[str] = []
    if not spec.title.strip():
        problems.append("the specification has no title")
    if not spec.criteria:
        problems.append("it has no acceptance criteria")
    seen: set[str] = set()
    for criterion in spec.criteria:
        if not CRITERION_ID.fullmatch(criterion.id):
            problems.append(f"criterion id {criterion.id!r} must look like AC-1, AC-2, ...")
        elif criterion.id in seen:
            problems.append(f"criterion id {criterion.id} is used twice")
        seen.add(criterion.id)
        if not criterion.text.strip():
            problems.append(f"criterion {criterion.id} has no text")
    return problems


def check_spec(spec: Spec) -> Spec:
    """``spec`` itself, or :class:`SpecError` saying how to fix it."""

    problems = spec_problems(spec)
    if problems:
        raise SpecError(
            "The specification is not usable: " + "; ".join(problems) + ". Number the acceptance "
            "criteria AC-1, AC-2, ... (each id once) and give each one checkable text."
        )
    return spec


def render_spec(spec: Spec) -> str:
    """``docs/spec.md``: the contract in a form people (and later stages) read."""

    lines = [f"# {spec.title.strip()}", ""]
    if spec.summary.strip():
        lines += [spec.summary.strip(), ""]
    lines += ["## Acceptance criteria", ""]
    lines += [f"- **{c.id}** ({c.kind}): {_one_line(c.text)}" for c in spec.criteria]
    for heading, items in (
        ("Non-goals", spec.non_goals),
        ("Assumptions", spec.assumptions),
        ("Clarifications from the person running the team", spec.clarifications),
        ("Open questions", spec.open_questions),
    ):
        if items:
            lines += ["", f"## {heading}", "", *(f"- {_one_line(item)}" for item in items)]
    lines += ["", f"Analyst confidence: {spec.confidence}", ""]
    return "\n".join(lines)


def needs_clarification(spec: Spec) -> bool:
    return spec.confidence == "low" or bool(_questions(spec))


def _questions(spec: Spec) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for question in spec.blocking_questions:
        cleaned = " ".join(question.split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            found.append(cleaned)
    return found


def _one_line(text: str) -> str:
    return " ".join(text.split())


class Clarifier:
    """Settles a spec's blocking questions: asks the person, or records assumptions."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    def settle(self, spec: Spec, revise: Callable[[str], Spec]) -> Spec:
        """Return ``spec`` with its blocking questions resolved.

        With a human available (``--interactive``) at most ``intake.max_questions`` questions
        are asked, and when any is answered ``revise(note)`` asks the analyst for the final
        specification with the answers built in (if that fails, the answers are recorded
        as they are). Unanswered questions, and every question of a non-interactive run, become
        recorded assumptions: the run continues.
        """

        if not needs_clarification(spec):
            return spec
        questions = _questions(spec)
        events = self.ctx.events
        if not self.ctx.human.interactive:
            events.emit("spec.assumed", questions=len(questions), low=spec.confidence == "low")
            return self._assume(spec, questions, "no one to ask")
        settings = self.ctx.settings.intake
        asked = questions[: settings.max_questions]
        answered: dict[str, str] = {}
        for question in asked:
            answer = self.ctx.human.ask(
                question,
                agent=ANALYST,
                timeout=float(settings.question_timeout_seconds),
                cancel_event=self.ctx.cancel_event,
            )
            if answer and answer.strip():
                answered[question] = " ".join(answer.split())
        remaining = [q for q in questions if q not in answered]
        events.emit("spec.clarified", asked=len(asked), answered=len(answered))
        if not answered:
            return self._assume(spec, remaining, "no answer")
        facts = [f"Q: {q} A: {a}" for q, a in answered.items()]
        revised = self._revise(spec, facts, remaining, revise)
        return self._assume(
            revised.model_copy(update={"clarifications": [*revised.clarifications, *facts]}),
            remaining,
            "no answer",
        )

    def _revise(
        self, spec: Spec, facts: list[str], remaining: list[str], revise: Callable[[str], Spec]
    ) -> Spec:
        note = (
            "The person running the team answered your open questions:\n"
            + "\n".join(f"- {fact}" for fact in facts)
            + "\nReturn the final specification with these answers built into the criteria, "
            "assumptions, and non-goals. Do not ask them again and leave blocking_questions empty."
        )
        if remaining:
            note += (
                " These were not answered; state your own assumption for each under assumptions: "
                + "; ".join(remaining)
            )
        try:
            return revise(note)
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # keep what the analyst first produced; the answers are recorded
            self.ctx.events.emit("spec.revise_failed", error=str(exc)[:300])
            return spec

    @staticmethod
    def _assume(spec: Spec, questions: list[str], why: str) -> Spec:
        """Record ``questions`` as open and assumed (``why`` they were not answered)."""

        assumptions = list(spec.assumptions)
        if spec.confidence == "low" and LOW_CONFIDENCE not in assumptions:
            assumptions.append(LOW_CONFIDENCE)
        opened = list(spec.open_questions)
        for question in questions:
            assumptions.append(f"Not confirmed ({why}), so the team assumes the above: {question}")
            if question not in opened:
                opened.append(question)
        return spec.model_copy(
            update={"assumptions": assumptions, "open_questions": opened, "blocking_questions": []}
        )
