"""The pipeline's typed state, its file, and what a strategy hands back.

``PipelineState`` is the Flow's state and is persisted to ``runs/<run_id>/pipeline.json`` after
every stage and work package. It holds the contracts stages hand to each other and the
bookkeeping only the pipeline needs; *where each stage stands* lives in the manifest's stage
records (one source of truth) because that is what resume checks against the workspace.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import (
    CheckResult,
    Contract,
    Plan,
    RunStatus,
    Spec,
    StageRecord,
)

STATE_FILENAME = "pipeline.json"


def request_hash(requirements: str) -> str:
    """The hash recorded in the manifest, so a changed request is never mistaken for the old one."""

    return hashlib.sha256(requirements.strip().encode("utf-8")).hexdigest()


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
    checks: list[CheckResult] = Field(default_factory=list)  # filled by the verifier (later)
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


@dataclass(frozen=True)
class RunResult:
    """How a strategy ended. ``status`` is the run's outcome as recorded in its manifest."""

    run_id: str
    status: RunStatus
    error: str = ""
    stages: list[StageRecord] = field(default_factory=list)
    workspace: Path | None = None
