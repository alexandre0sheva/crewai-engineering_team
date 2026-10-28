"""The release's live spend cap: ``budget.toml`` and the append-only ledger ``spend.jsonl``.

Both live in the suite directory (``benchmarks/``). ``budget.toml`` holds the cap for the release
and one allocation per plan task (``T25 = 2.80``); ``spend.jsonl`` has one line per live command:
when, which allocation, the command, the estimate, and the actual cost (``null``: unknown, charged
as the estimate). A live ``bench`` command checks the ledger before it starts and refuses, with the
numbers, when the estimate or its own cap would take an allocation or the total over. Runs billed
by a subscription (``billing: subscription``) are recorded but never charged. See
docs/BENCHMARKS.md ("Live spend cap").
"""

from __future__ import annotations

import json
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from engineering_team.bench.tasks import BenchError

BUDGET_FILE = "budget.toml"
LEDGER_FILE = "spend.jsonl"
EPSILON = 1e-9


class SpendRefused(BenchError):
    """A live command that would go over an allocation or the release cap; says by how much."""


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: str = ""
    total_usd: float = Field(gt=0)
    allocations: dict[str, float] = Field(min_length=1)

    @model_validator(mode="after")
    def _fits(self) -> Budget:
        if any(amount < 0 for amount in self.allocations.values()):
            raise ValueError("allocations cannot be negative")
        if sum(self.allocations.values()) > self.total_usd + EPSILON:
            raise ValueError(
                f"the allocations add up to {sum(self.allocations.values()):.2f}, "
                f"more than total_usd {self.total_usd:.2f}"
            )
        return self


class SpendEntry(BaseModel):
    """One live command: what it was expected to cost and what it did."""

    date: str
    task: str  # the allocation it is charged to (a plan task such as ``T25``)
    command: str
    estimated_usd: float = Field(ge=0)
    actual_usd: float | None = Field(default=None, ge=0)  # None: unknown, charged as the estimate
    billing: Literal["api", "subscription"] = "api"
    batch: str | None = None
    note: str = ""

    @property
    def charged(self) -> float:
        if self.billing == "subscription":
            return 0.0
        return self.estimated_usd if self.actual_usd is None else self.actual_usd


class SpendLedger:
    """``budget.toml`` and ``spend.jsonl`` of one suite directory."""

    def __init__(self, suite: Path) -> None:
        self.budget_path = suite / BUDGET_FILE
        self.ledger_path = suite / LEDGER_FILE

    def budget(self) -> Budget:
        try:
            data = tomllib.loads(self.budget_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise BenchError(
                f"No {self.budget_path}: a live run needs the release budget (total_usd and "
                "[allocations]); see docs/BENCHMARKS.md."
            ) from None
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise BenchError(f"Cannot read {self.budget_path}: {exc}") from exc
        try:
            return Budget.model_validate(data)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'budget'}: {e['msg']}" for e in exc.errors()
            )
            raise BenchError(f"{self.budget_path}: {details}") from exc

    def entries(self) -> list[SpendEntry]:
        if not self.ledger_path.is_file():
            return []
        found = []
        for number, line in enumerate(self.ledger_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                found.append(SpendEntry.model_validate_json(line))
            except ValidationError as exc:
                raise BenchError(
                    f"{self.ledger_path}:{number} is not a spend entry ({exc.error_count()} "
                    "problem(s)); the ledger is append-only: fix that line by hand."
                ) from exc
        return found

    def spent(self, task: str | None = None) -> float:
        return sum(e.charged for e in self.entries() if task is None or e.task == task)

    def check(self, task: str, estimate: float, cap: float | None = None) -> None:
        """Refuse (``SpendRefused``) a live command charged to ``task`` that is expected to cost
        ``estimate`` and may cost up to ``cap`` when either would overspend."""

        budget = self.budget()
        if task not in budget.allocations:
            raise SpendRefused(
                f"No allocation {task!r} in {self.budget_path} (there are: "
                f"{', '.join(budget.allocations)}). Live spend is charged to a plan task."
            )
        allocation, spent = budget.allocations[task], self.spent(task)
        left = allocation - spent
        total_left = budget.total_usd - self.spent()
        if estimate > left + EPSILON:
            raise SpendRefused(
                f"{task} has spent ${spent:.2f} of its ${allocation:.2f} (${left:.2f} left); this "
                f"is estimated at ${estimate:.2f}. Going over an allocation needs the user's OK "
                f"(then move unspent money into {task} in {BUDGET_FILE})."
            )
        if estimate > total_left + EPSILON:
            raise SpendRefused(
                f"The release has ${total_left:.2f} of ${budget.total_usd:.2f} left; this is "
                f"estimated at ${estimate:.2f}."
            )
        if cap is not None and cap > min(left, total_left) + EPSILON:
            raise SpendRefused(
                f"The cap ${cap:.2f} is more than the ${min(left, total_left):.2f} left in {task}; "
                "lower --budget-usd so an over-running batch cannot overspend."
            )

    def record(self, entry: SpendEntry) -> None:
        """Append one line (and flush it to disk): the ledger is never rewritten."""

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(mode="json")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def rows(self) -> list[dict[str, object]]:
        """Per allocation: allocated, spent, remaining, live commands, subscription runs."""

        budget, entries = self.budget(), self.entries()
        names = list(budget.allocations) + sorted(
            {e.task for e in entries} - set(budget.allocations)
        )
        return [
            {
                "task": name,
                "allocated_usd": budget.allocations.get(name, 0.0),
                "spent_usd": round(sum(e.charged for e in entries if e.task == name), 6),
                "remaining_usd": round(
                    budget.allocations.get(name, 0.0)
                    - sum(e.charged for e in entries if e.task == name),
                    6,
                ),
                "commands": sum(1 for e in entries if e.task == name and e.billing == "api"),
                "unknown_cost": sum(
                    1 for e in entries if e.task == name and e.billing == "api" and
                    e.actual_usd is None
                ),
                "subscription_runs": sum(
                    1 for e in entries if e.task == name and e.billing == "subscription"
                ),
            }
            for name in names
        ]  # fmt: skip


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
