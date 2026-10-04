"""The data a run report shows, gathered once from the run directory and rendered twice.

Everything here is read from files the controller wrote (manifest, events, board, pipeline
state, usage, the patch). Text an agent or the repository produced is kept apart from what the
controller measured, and both renderers escape it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from engineering_team.board.models import BoardProgress, Card
from engineering_team.contracts import (
    BudgetStatus,
    CheckResult,
    CriterionCoverage,
    Finding,
    UsageReport,
)

Tone = Literal["good", "warn", "bad", "info"]


@dataclass(frozen=True)
class Banner:
    """The one-line standing at the top: ``label`` (e.g. "Failed"), its tone, and why."""

    label: str
    tone: Tone
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Notice:
    tone: Tone
    text: str


@dataclass(frozen=True)
class StageRow:
    name: str
    status: str
    attempts: int
    start: float | None  # seconds after the run was created
    end: float | None
    detail: str = ""


@dataclass(frozen=True)
class LaneBar:
    """One unit of parallel work (a work package or a read-only job) in its lane."""

    stage: str
    lane: str
    unit: str
    status: str
    start: float
    end: float
    error: str = ""


@dataclass(frozen=True)
class AgentActivity:
    """What one teammate did, counted from ``tool.call`` events and the board."""

    agent: str
    tool_calls: int
    failed_calls: int
    seconds: float
    cards: list[str] = field(default_factory=list)  # ids of the cards assigned to it
    tools: dict[str, int] = field(default_factory=dict)  # tool -> calls, most used first


@dataclass(frozen=True)
class Screenshot:
    name: str
    path: str  # relative to the run directory
    agent: str = ""
    url: str = ""
    size: int = 0


@dataclass(frozen=True)
class DiffFile:
    path: str
    status: str  # added | modified | deleted | renamed
    added: int
    removed: int
    lines: list[str] = field(default_factory=list)  # the unified diff of this file
    omitted: int = 0  # lines left out of ``lines``
    binary: bool = False


@dataclass(frozen=True)
class DiffView:
    files: list[DiffFile]
    added: int
    removed: int
    patch_name: str
    truncated: bool = False


@dataclass
class RunReport:
    run_id: str
    project: str
    mode: str
    strategy: str
    recipe: str | None
    status: str
    verdict: str | None
    created: datetime
    finished: datetime | None
    duration_seconds: float
    banner: Banner
    request: str = ""
    resumes: int = 0
    stages: list[StageRow] = field(default_factory=list)
    lanes: list[LaneBar] = field(default_factory=list)
    timeline_seconds: float = 0.0
    cards: list[Card] = field(default_factory=list)
    progress: BoardProgress | None = None
    paused: bool = False
    agents: list[AgentActivity] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    usage: UsageReport | None = None
    budget: BudgetStatus | None = None
    tool_calls: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    coverage: list[CriterionCoverage] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    diff: DiffView | None = None
    diff_note: str = ""
    warnings: list[Notice] = field(default_factory=list)
    environment: list[tuple[str, str]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    summaries: dict[str, str] = field(default_factory=dict)  # stage -> what its agent said
