"""Typed contracts shared by the controller, the run store, and (later) the agents.

Every model carries ``schema_version`` and tolerates unknown fields (they are ignored), so a
manifest or event written by a newer version still loads. Bump ``schema_version`` only for a
breaking change to a model's meaning; adding an optional field is not one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "interrupted"]
StageStatus = Literal[
    "pending", "running", "succeeded", "failed", "skipped", "cancelled", "interrupted"
]
CheckStatus = Literal["passed", "failed", "skipped", "unavailable"]
Severity = Literal["info", "low", "medium", "high", "critical"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Contract(BaseModel):
    """Base for every contract: versioned, and tolerant of fields it does not know."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION


# -- product definition ------------------------------------------------------------------


class AcceptanceCriterion(Contract):
    id: str
    text: str
    kind: str = "functional"  # e.g. functional, quality, constraint, security


class Spec(Contract):
    """What to build, in testable terms."""

    title: str
    summary: str = ""
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ProjectCommands(Contract):
    """Command lines for the project, each a list so a stack can need several."""

    setup: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    build: list[str] = Field(default_factory=list)
    run: list[str] = Field(default_factory=list)


class WorkPackage(Contract):
    """A unit of work for one teammate, with the paths it owns."""

    id: str
    title: str
    role: str
    description: str = ""
    owned_paths: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    criteria_ids: list[str] = Field(default_factory=list)
    required: bool = True  # a failed optional package does not fail the run


class Plan(Contract):
    stack: str = ""
    commands: ProjectCommands = Field(default_factory=ProjectCommands)
    work_packages: list[WorkPackage] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


# -- verification ------------------------------------------------------------------------


class CheckSpec(Contract):
    """An independent check the controller runs (never an agent)."""

    id: str
    name: str
    argv: list[str]
    required: bool = True
    timeout: float = 120.0
    criteria_ids: list[str] = Field(default_factory=list)


class CheckResult(Contract):
    id: str
    status: CheckStatus
    exit_code: int | None = None
    duration: float = 0.0
    log_path: str | None = None
    revision: str | None = None  # workspace tree hash the check ran against
    started_at: datetime | None = None


class Finding(Contract):
    id: str
    severity: Severity
    summary: str
    file: str | None = None
    line: int | None = None
    suggested_fix: str | None = None
    source_role: str | None = None


# -- run bookkeeping ---------------------------------------------------------------------


class StageRecord(Contract):
    """One stage's standing. ``revision_start``/``revision`` are the workspace tree hash when the
    stage began and when it ended; resume trusts a finished stage only while they still fit the
    workspace. ``detail`` says why a stage was skipped, failed, or was cancelled."""

    name: str
    status: StageStatus = "pending"
    started: datetime | None = None
    finished: datetime | None = None
    attempts: int = 0
    artifacts: list[str] = Field(default_factory=list)
    revision_start: str | None = None
    revision: str | None = None
    detail: str = ""


class UsageTotals(Contract):
    """Token counts. ``prompt_tokens`` is the full billed prompt (it includes cached tokens)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class UsageRow(UsageTotals):
    """Usage of one model by one agent in one stage, with its estimated cost."""

    stage: str | None = None
    agent: str | None = None
    model: str = ""
    cost_usd: float | None = None  # None: the model has no known price


class UsageReport(Contract):
    """Everything ``usage.json`` holds. ``estimated_cost_usd`` is ``None`` unless *every* model
    that was used has a known price: a partial sum would understate the cost."""

    totals: UsageTotals = Field(default_factory=UsageTotals)
    tool_calls: int = 0
    estimated_cost_usd: float | None = None
    known_cost_usd: float = 0.0
    unpriced_models: list[str] = Field(default_factory=list)
    by_model: dict[str, UsageTotals] = Field(default_factory=dict)
    by_agent: dict[str, UsageTotals] = Field(default_factory=dict)
    by_stage: dict[str, UsageTotals] = Field(default_factory=dict)
    cost_by_model: dict[str, float | None] = Field(default_factory=dict)
    rows: list[UsageRow] = Field(default_factory=list)


BudgetState = Literal["unlimited", "ok", "warning", "exceeded"]


class BudgetLimitStatus(Contract):
    name: str  # max_cost_usd, max_tokens, max_wall_seconds, max_tool_calls
    used: float
    max: float
    fraction: float


class BudgetStatus(Contract):
    state: BudgetState = "unlimited"
    limits: list[BudgetLimitStatus] = Field(default_factory=list)
    exceeded: str | None = None  # why the run was stopped
    notes: list[str] = Field(default_factory=list)  # e.g. a cost limit that cannot be enforced


class RunSummary(Contract):
    """What a run cost, in one place; also stored in the manifest."""

    status: RunStatus
    duration_seconds: float = 0.0
    usage: UsageTotals = Field(default_factory=UsageTotals)
    tool_calls: int = 0
    estimated_cost_usd: float | None = None
    unpriced_models: list[str] = Field(default_factory=list)
    budget_status: BudgetStatus = Field(default_factory=BudgetStatus)


class RunManifest(Contract):
    """Everything needed to identify a run: what it was asked, with which settings and
    versions, how it went, and when. Written to ``runs/<run_id>/manifest.json``."""

    run_id: str
    project_name: str
    mode: str = "build"
    request_hash: str = ""
    settings_hash: str = ""
    versions: dict[str, str] = Field(default_factory=dict)
    strategy: str = "hierarchical"
    recipe: str | None = None
    status: RunStatus = "pending"
    resumes: int = 0  # how many times `resume` continued this run
    stages: list[StageRecord] = Field(default_factory=list)
    created: datetime = Field(default_factory=utc_now)
    finished: datetime | None = None
    summary: RunSummary | None = None


class Event(Contract):
    """One line of ``events.jsonl``. ``seq`` increases by one per run, in file order."""

    seq: int
    ts: datetime
    run_id: str
    type: str
    stage: str | None = None
    agent: str | None = None
    lane: int | str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
