"""The pipeline's typed state, its file, and what a strategy hands back.

``PipelineState`` is the Flow's state and is persisted to ``runs/<run_id>/pipeline.json`` after
every stage and work package. It holds the contracts stages hand to each other and the
bookkeeping only the pipeline needs; *where each stage stands* lives in the manifest's stage
records (one source of truth) because that is what resume checks against the workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import (
    CheckResult,
    Contract,
    Finding,
    Plan,
    RunStatus,
    RunVerdict,
    Spec,
    StageRecord,
    VerificationRecord,
)
from engineering_team.intake.bundle import request_hash as request_hash  # re-exported
from engineering_team.modes.baseline_report import BaselineReport
from engineering_team.modes.diff_noise import DiffNoise
from engineering_team.modes.fix_contracts import FixNote, FixRecord, Repro, Triage
from engineering_team.modes.maintain_contracts import (
    AuditNote,
    CoverageDelta,
    UpgradeOutcome,
    UpgradePlan,
)
from engineering_team.modes.repo_profile import RepoProfile

STATE_FILENAME = "pipeline.json"

PackageStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]


class PackageState(BaseModel):
    """Where one work package of the plan stands."""

    model_config = ConfigDict(extra="ignore")

    status: PackageStatus = "pending"
    card_id: str | None = None
    attempts: int = 0
    summary: str = ""
    error: str = ""


class PipelineState(Contract):
    """The Flow's state (the Flow adds its own ``id``; it is not persisted)."""

    request_hash: str = ""
    recipe: str = ""
    recipe_digest: str = ""
    spec: Spec | None = None
    plan: Plan | None = None
    packages: dict[str, PackageState] = Field(default_factory=dict)
    stage_cards: dict[str, str] = Field(default_factory=dict)  # stage name -> board card id
    summaries: dict[str, str] = Field(default_factory=dict)  # stage name -> what its agent said
    checks: list[CheckResult] = Field(default_factory=list)  # the verifier's latest results
    verification: VerificationRecord = Field(default_factory=VerificationRecord)
    findings: list[Finding] = Field(default_factory=list)  # the review stage's consolidated ones
    profile: RepoProfile | None = None  # the adopt recipe's deterministic repository analysis
    baseline: BaselineReport | None = None  # what the project's checks said before any change
    isolation: dict[str, Any] | None = None  # how the team got its workspace (`Isolation`, JSON)
    diff_noise: DiffNoise | None = None  # the latest measure of changes outside the plan's scope
    # Fix mode: the debugger's accounts (not evidence), and what the controller itself saw.
    triage: Triage | None = None
    repro: Repro | None = None
    fix_note: FixNote | None = None
    fix: FixRecord | None = None
    needs_info: list[str] = Field(default_factory=list)  # questions when the bug is not reproduced
    # What the command line asked for beyond the request (``modes/run_options.py``); conditions
    # such as ``fixes_not_requested`` read it. Loaded again on resume.
    options: dict[str, Any] = Field(default_factory=dict)
    # Maintain mode: coverage before and after, the planned dependency upgrades and what the
    # controller found trying them, and the dependency audits it ran.
    coverage: CoverageDelta | None = None
    upgrades: UpgradePlan | None = None
    upgrade_outcomes: list[UpgradeOutcome] = Field(default_factory=list)
    audit: list[AuditNote] = Field(default_factory=list)
    audit_findings: list[Finding] = Field(default_factory=list)  # from the dependency audit
    error: str = ""

    @classmethod
    def load(cls, run_dir: Path) -> PipelineState | None:
        try:
            return cls.model_validate_json((run_dir / STATE_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, run_dir: Path) -> None:
        atomic_write_json(run_dir / STATE_FILENAME, self.model_dump(mode="json", exclude={"id"}))


@dataclass(frozen=True)
class RunBundle:
    """Everything a strategy needs to know about the request it is run for."""

    requirements: str
    inputs: dict[str, str] = field(default_factory=dict)
    resume: bool = False
    checks_digest: str = ""  # the pinned user checks file (``--checks``); see verification/
    script_digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """How a strategy ended. ``status`` is the run's outcome as recorded in its manifest."""

    run_id: str
    status: RunStatus
    error: str = ""
    stages: list[StageRecord] = field(default_factory=list)
    workspace: Path | None = None
    verdict: RunVerdict | None = (
        None  # set when the run verified: ``verified``, ``failed``, ``partial``, ``needs-info``
    )
