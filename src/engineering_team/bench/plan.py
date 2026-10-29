"""What a batch plans to run: the run specs, the options every run shares, and the plan."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.bench.agents import AGENT_NAMES
from engineering_team.bench.tasks import BenchTask

STRATEGIES = ("pipeline", "single")


def is_agent(contender: str) -> bool:
    return contender in AGENT_NAMES


def applies(task: BenchTask, contender: str) -> bool:
    """Whether a task can be run by this strategy or external agent. The agents take every task
    except reviews, which end in a findings file only the team's ``review`` command writes."""

    if is_agent(contender):
        return task.mode != "review"
    return task.applies_to(contender)


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
    fake_solution: str = "reference"  # fake only: "reference", "broken" (sabotaged) or "fixture"
    run_budget_usd: float | None = None  # the most one live run may spend
    python: str = sys.executable
    acceptance_timeout: float = 120.0
    acceptance_sandbox: str = "docker"  # where Node/Go checks run: docker or local
    node_modules: str | None = None  # local Node checks: the task packages' node_modules
    resume: bool = False
    agent_models: Mapping[str, str] = field(
        default_factory=dict
    )  # agent -> the model it is pinned to
    agent_billing: Mapping[str, str] = field(default_factory=dict)  # agent -> api or subscription
    agent_versions: Mapping[str, str] = field(default_factory=dict)

    def cap_for(self, contender: str) -> float | None:
        """The most a run by ``contender`` may spend; ``None``: it spends no money (an offline
        team, or an agent on a plan)."""

        if is_agent(contender):
            return (
                None if self.agent_billing.get(contender) == "subscription" else self.run_budget_usd
            )
        return None if self.fake else self.run_budget_usd


def plan_runs(
    tasks: Sequence[BenchTask], strategies: Sequence[str], repeat: int
) -> tuple[list[RunSpec], list[tuple[str, str]]]:
    """The runs to make, ordered by task, strategy, repeat; and the (task, strategy) pairs that
    cannot run (the repository modes use the pipeline only; external agents skip reviews)."""

    specs: list[RunSpec] = []
    skipped: list[tuple[str, str]] = []
    for task in sorted(tasks, key=lambda t: t.id):
        for strategy in strategies:
            if not applies(task, strategy):
                skipped.append((task.id, strategy))
                continue
            specs.extend(RunSpec(task, strategy, n) for n in range(1, repeat + 1))
    return specs, skipped
