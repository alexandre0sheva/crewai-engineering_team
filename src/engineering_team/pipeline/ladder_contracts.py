"""What the ``auto`` strategy keeps about its escalation ladder (``ladder.py``): one record per
rung the run climbed, in ``runs/<id>/ladder.json``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import Contract, utc_now
from engineering_team.settings.sections import RungName

LADDER_FILENAME = "ladder.json"

RungStatus = Literal["running", "succeeded", "escalated"]


class RungRecord(Contract):
    """One rung the run climbed: why it ran, how it ended, what it cost.

    ``carried`` and ``escalation`` are what an escalated rung starts its state with, kept here so
    a run that stops before the rung's first save starts it the same way on resume."""

    rung: RungName
    recipe: str
    status: RungStatus = "running"
    why: str
    outcome: str = ""
    started: datetime = Field(default_factory=utc_now)
    finished: datetime | None = None
    estimate_usd: float | None = None
    spent_before_usd: float = 0.0  # the run's known cost when the rung started
    cost_usd: float | None = None
    failing_checks: list[str] = Field(default_factory=list)
    trace_from: int = 0  # the first trajectory entry of this rung
    carried: dict[str, Any] = Field(default_factory=dict)
    escalation: str = ""


class LadderRecord(Contract):
    """The ladder of one ``auto`` run (``runs/<id>/ladder.json``)."""

    start: RungName
    max_rung: RungName
    lowest: RungName = "solo"  # ``pipeline`` when a lead cannot run the mode's recipe
    reasons: list[str] = Field(default_factory=list)  # why it started where it did
    rungs: list[RungRecord] = Field(default_factory=list)
    stopped: str = ""  # why it could not climb further, when it had to stop

    @property
    def current(self) -> RungRecord | None:
        return self.rungs[-1] if self.rungs else None

    @classmethod
    def load(cls, run_dir: Path) -> LadderRecord | None:
        try:
            return cls.model_validate_json((run_dir / LADDER_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, run_dir: Path) -> None:
        atomic_write_json(run_dir / LADDER_FILENAME, self.model_dump(mode="json"))
