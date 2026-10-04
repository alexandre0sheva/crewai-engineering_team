"""What a batch plans to run: the run specs, the options every run shares, and the plan."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from engineering_team.bench.tasks import BenchTask

STRATEGIES = ("pipeline", "hierarchical", "single")


@dataclass(frozen=True)
class RunSpec:
    task: BenchTask
    strategy: str
    repeat: int

    @property
    def key(self) -> str:
        return f"{self.task.id}/{self.strategy}-{self.repeat}"


@dataclass(frozen=True)
class RunOptions:
    batch: str
    batch_dir: Path
    provider: str | None = None
    profile: str | None = None
    sandbox: str | None = None
    config: str | None = None  # a settings file for the team (the only way to vary more than flags)
    fake: bool = False
    fake_solution: str = "reference"  # fake only: "reference" or "broken" (the sabotaged one)
    run_budget_usd: float | None = None  # the most one live run may spend
    python: str = sys.executable
    acceptance_timeout: float = 120.0
    resume: bool = False


def plan_runs(
    tasks: Sequence[BenchTask], strategies: Sequence[str], repeat: int
) -> tuple[list[RunSpec], list[tuple[str, str]]]:
    """The runs to make, ordered by task, strategy, repeat; and the (task, strategy) pairs that
    cannot run (the repository modes use the pipeline only)."""

    specs: list[RunSpec] = []
    skipped: list[tuple[str, str]] = []
    for task in sorted(tasks, key=lambda t: t.id):
        for strategy in strategies:
            if not task.applies_to(strategy):
                skipped.append((task.id, strategy))
                continue
            specs.extend(RunSpec(task, strategy, n) for n in range(1, repeat + 1))
    return specs, skipped
