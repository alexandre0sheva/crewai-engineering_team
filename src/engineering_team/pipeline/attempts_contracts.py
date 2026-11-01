"""What a best-of-N run keeps about its attempts (``attempts.py``): ``runs/<id>/attempts.json``.

The record is also the attempts' cost ledger: one entry per attempt and one for the judge, each
with what it cost. ``ledger_usd`` is their sum and ``spent_usd`` what the run's own usage grew by
while the attempts and the judge ran; the two agree when every model call was accounted for (the
**ledger identity**, which the report shows).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import Contract, utc_now

ATTEMPTS_FILENAME = "attempts.json"
ATTEMPTS_DIR = "attempts"  # each attempt's run directory and patch, as attempts/<label>/

AttemptStatus = Literal["pending", "running", "passed", "failed"]
# How the result was chosen: the judge; the only attempt that passed; the smallest passing diff
# because the judge failed (or could not be paid for); none passed, so the closest is reported.
SelectionMethod = Literal["judge", "only", "fallback", "none-passed", "stopped"]


class AttemptRecord(Contract):
    """One attempt: how it varied, how its run ended, what the controller's checks said."""

    label: str  # a1, a2, ... in the order the attempts were started
    run_id: str
    vary: str  # what made it differ, e.g. "seed 2", "worker model openai/x", "harness bundled"
    status: AttemptStatus = "pending"
    outcome: str = ""  # the attempt run's status and error, or its verdict
    verdict: str | None = None
    checks_passed: int = 0
    checks_total: int = 0
    failing_checks: list[str] = Field(default_factory=list)
    findings: int = 0  # the review findings its run recorded
    files_changed: int = 0
    diff_lines: int = 0  # added plus removed lines (a binary or unstored file counts as one)
    revision: str = ""  # the attempt's tree hash when its run ended
    cost_usd: float | None = None  # None: a model without a price, never free
    started: datetime | None = None
    finished: datetime | None = None
    run_dir: str = ""  # relative to the run directory: attempts/<label>/run
    patch: str = ""  # relative to the run directory: attempts/<label>.patch

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class JudgeScore(Contract):
    """The judge's rubric scores for one anonymised candidate (0 to 5 each)."""

    correctness: int = Field(ge=0, le=5)
    minimality: int = Field(ge=0, le=5)
    consistency: int = Field(ge=0, le=5)
    tests: int = Field(ge=0, le=5)

    @property
    def total(self) -> int:
        return self.correctness + self.minimality + self.consistency + self.tests


class Selection(Contract):
    """Which attempt became the run's result, how, and why."""

    method: SelectionMethod
    chosen: str | None = None  # an attempt label
    reason: str = ""
    anonymised: dict[str, str] = Field(default_factory=dict)  # attempt label -> candidate id
    scores: dict[str, JudgeScore] = Field(default_factory=dict)  # attempt label -> scores
    judge_model: str = ""
    judge_error: str = ""  # why the judge's answer was not used, when it was not
    judge_cost_usd: float | None = 0.0
    revision_match: bool | None = None  # the run's tree after applying is the attempt's


class AttemptsRecord(Contract):
    """The attempts of one run (``runs/<id>/attempts.json``)."""

    count: int
    vary: str
    source: Literal["option", "ladder"] = "option"  # --attempts, or the ladder's last rung
    recipe: str = ""
    estimate_usd: float | None = None  # all attempts and the judge, before they started
    started: datetime = Field(default_factory=utc_now)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    selection: Selection | None = None
    spent_usd: float | None = None  # what the run's usage grew by during the attempts
    stopped: str = ""  # why the attempts could not start or finish

    @property
    def ledger_usd(self) -> float | None:
        """The ledger's total: every attempt plus the judge (``None`` if any is unknown)."""

        costs = [a.cost_usd for a in self.attempts]
        costs.append(self.selection.judge_cost_usd if self.selection else 0.0)
        if any(cost is None for cost in costs):
            return None
        return round(sum(cost for cost in costs if cost is not None), 6)

    def attempt(self, label: str) -> AttemptRecord | None:
        return next((a for a in self.attempts if a.label == label), None)

    @classmethod
    def load(cls, run_dir: Path) -> AttemptsRecord | None:
        try:
            return cls.model_validate_json(
                (run_dir / ATTEMPTS_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def save(self, run_dir: Path) -> None:
        atomic_write_json(run_dir / ATTEMPTS_FILENAME, self.model_dump(mode="json"))
