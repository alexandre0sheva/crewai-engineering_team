"""The contracts of ``fix`` mode: what the debugger hands over, and what the controller records.

``Triage``, ``Repro`` and ``FixNote`` are the agents' (the recipe's ``triage``, ``reproduce`` and
``fix`` stages return them); they are accounts, never evidence. ``FixRecord`` is the controller's:
the reproduction it ran itself, red before the fix and green after it, kept in the pipeline state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engineering_team.contracts import Confidence, Contract, utc_now
from engineering_team.modes.trace import ParsedTrace


class Hypothesis(Contract):
    """One possible cause of the bug, with where to look."""

    summary: str
    suspects: list[str] = Field(default_factory=list)  # project files, relative paths
    evidence: str = ""  # what in the trace, the code, or the history points here
    likelihood: Confidence = "medium"


class Triage(Contract):
    """The debugger's first look: what the bug is and where the cause may be, most likely first."""

    title: str
    summary: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    suspect_files: list[str] = Field(default_factory=list)
    reproduction_idea: str = ""  # how a failing test or script could show it


class Repro(Contract):
    """A reproduction the debugger wrote: a test or script that fails because of the bug.

    ``command`` is one command line (no shell operators) that runs it; the controller runs it
    itself and only a real failure counts as reproduced. ``questions`` are for the person, when
    the bug cannot be reproduced from what was given.
    """

    command: str = ""
    files: list[str] = Field(default_factory=list)  # the test or script files written for it
    kind: Literal["test", "script"] = "test"
    expected_failure: str = ""  # what the failure should look like, for the record
    summary: str = ""
    questions: list[str] = Field(default_factory=list)


class FixNote(Contract):
    """The debugger's account of the fix: for the summary, not evidence."""

    root_cause: str
    change: str = ""
    risk: str = ""
    files: list[str] = Field(default_factory=list)


class ReproRun(Contract):
    """One run of the reproduction by the controller."""

    phase: Literal["red", "green"]
    command: str
    status: Literal["failed", "passed", "unavailable"]
    exit_code: int | None = None
    summary: str = ""
    log_path: str | None = None
    revision: str | None = None  # the tree it ran against
    attempt: int = 0
    at: str = Field(default_factory=lambda: utc_now().isoformat(timespec="seconds"))


class FixRecord(Contract):
    """What the controller established about the bug, from its own runs.

    ``reproduced`` is true only when the reproduction failed (``red``) on the tree before the
    fix; ``green`` is set once the same command passes in verification. ``digests`` pin the
    reproduction's files at the moment they were seen failing, so a later edit is noticed.
    """

    allow_unreproduced: bool = False
    user_repro: str | None = None  # the command the person gave (--repro)
    user_red: ReproRun | None = None  # what it did on the tree before any change
    trace: ParsedTrace | None = None
    suspects: list[str] = Field(default_factory=list)  # project files the trace names
    attempts: int = 0
    reproduced: bool = False
    command: str = ""
    files: list[str] = Field(default_factory=list)
    digests: dict[str, str] = Field(default_factory=dict)
    red: ReproRun | None = None
    green: ReproRun | None = None
    notes: list[str] = Field(default_factory=list)  # why attempts did not reproduce it
