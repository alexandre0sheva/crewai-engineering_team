"""The answer path's contracts: an ``Answer`` with citations, and what the controller found when it
checked them (``modes/citations.py``). The agent's answer is its own account; the check is the
controller's."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engineering_team.contracts import Confidence, Contract

ANSWER_FILE = "ANSWER.md"
PLAN_FILE = "PLAN.md"

CitationStatus = Literal[
    "verified",
    "missing_file",  # no such file at the run's revision
    "outside_project",  # a path outside the project, or the controller's own state
    "bad_lines",  # the line range is missing, malformed or past the end of the file
    "no_quote",  # a citation without the text it stands for proves nothing
    "quote_mismatch",  # the cited lines do not contain the quote
    "not_fetched",  # a URL no tool call of the run fetched
    "no_source",  # neither a path nor a URL
]


class Citation(Contract):
    """A source for a claim: lines of a file with the text quoted, or a page the run fetched.

    ``lines`` is ``12`` or ``12-18`` (1-based, inclusive). Sections refer to citations by their
    position, ``[1]`` for the first."""

    path: str = ""
    lines: str = ""
    quote: str = ""
    url: str = ""
    title: str = ""

    @property
    def kind(self) -> Literal["code", "web"]:
        return "web" if self.url and not self.path else "code"

    @property
    def target(self) -> str:
        if self.kind == "web":
            return self.url
        return f"{self.path}:{self.lines}" if self.lines else self.path


class AnswerSection(Contract):
    heading: str
    body: str


class Answer(Contract):
    """What an ``answer`` stage returns for a question or a piece of research."""

    summary: str
    sections: list[AnswerSection] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = "medium"
    open_questions: list[str] = Field(default_factory=list)


class CitationResult(Contract):
    number: int  # 1-based, as the sections cite it
    kind: Literal["code", "web"]
    target: str
    status: CitationStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "verified"


class AnswerCheck(Contract):
    """What the controller found when it checked an answer."""

    results: list[CitationResult] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)  # project files changed during the run
    attempts: int = 1

    @property
    def verified(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def unverified(self) -> list[CitationResult]:
        return [r for r in self.results if not r.ok]
