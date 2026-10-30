"""What the ``team`` strategy keeps in the run's state: each delegation, and the lead's record."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engineering_team.contracts import Contract, Finding

DelegationKind = Literal["delegate", "review", "security_review", "debug", "architect", "analyst"]
DelegationStatus = Literal["running", "succeeded", "failed", "cancelled", "interrupted"]


class DelegationRecord(Contract):
    """One job the lead gave a teammate, and what came of it (the controller's record).

    ``summary`` and ``findings`` are the teammate's own words; ``files_changed``, ``commands``,
    ``checks`` and the usage figures are what the controller saw."""

    id: str  # D1, D2, ... in the order the lead asked; also the job's lane
    kind: DelegationKind
    teammate: str
    brief: str
    why: str = ""
    owned_paths: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)
    status: DelegationStatus = "running"
    card_id: str | None = None
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    checks: str = ""  # the controller's checks after the job, when it changed files
    error: str = ""
    calls: int = 0
    tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0


class LeadRecord(Contract):
    """The lead stage's standing: its delegation cap, and the security signals the controller saw.

    ``security_reviewed`` is the workspace revision a security review last read and
    ``handed_back`` the revision the lead left; while they differ and a signal fired, the
    controller's own security review runs."""

    cap: int = 0
    signals: list[str] = Field(default_factory=list)
    security_reviewed: str | None = None
    handed_back: str | None = None
