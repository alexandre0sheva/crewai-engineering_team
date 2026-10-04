"""The contracts of ``maintain`` mode: coverage before and after, dependency upgrades, audits.

``Upgrade`` and ``UpgradePlan`` are the agent's (the planning stage returns them, never taken as
evidence of anything but what to try); everything else is the controller's own measurement.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engineering_team.contracts import Contract
from engineering_team.devtools.models import FileCoverage


class CoverageSnapshot(Contract):
    """Line coverage of the project's tests at one moment, from the project's own coverage tool."""

    status: Literal["measured", "unavailable"] = "unavailable"
    tool: str = ""
    covered: int = 0
    total: int = 0
    least_covered: list[FileCoverage] = Field(default_factory=list)
    note: str = ""  # why it is unavailable, or what to do about it

    @property
    def percent(self) -> float:
        return round(100.0 * self.covered / self.total, 1) if self.total else 0.0


class CoverageDelta(Contract):
    before: CoverageSnapshot | None = None
    after: CoverageSnapshot | None = None

    @property
    def change(self) -> float | None:
        """Percentage points gained, when both were measured."""

        if (
            self.before is None
            or self.after is None
            or self.before.status != "measured"
            or self.after.status != "measured"
        ):
            return None
        return round(self.after.percent - self.before.percent, 1)


class Upgrade(Contract):
    """One dependency to move to a newer version."""

    package: str
    manifest: str = ""  # the manifest that declares it, relative to the project
    current: str = ""
    target: str
    reason: str = ""  # why this version (a fix, a deprecation, the latest stable)

    @property
    def label(self) -> str:
        return f"{self.package} {self.current or '?'} -> {self.target}"


class UpgradePlan(Contract):
    """What the dependency analyst proposes to upgrade, and what it left alone."""

    upgrades: list[Upgrade] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)  # packages left alone, each with the reason


class UpgradeOutcome(Contract):
    """What the controller found when it tried an upgrade."""

    upgrade: Upgrade
    status: Literal["upgraded", "failed", "not-tried"]
    reason: str = ""  # for a failure: the check that said so, from the controller's own run
    group: int = 0  # the attempt it was tried in


class AuditNote(Contract):
    """One dependency audit the controller ran (or could not run)."""

    directory: str = "."
    tool: str = ""
    status: str = "unavailable"
    count: int = 0
    note: str = ""
