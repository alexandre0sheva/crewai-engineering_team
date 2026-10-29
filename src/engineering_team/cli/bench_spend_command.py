"""``bench spend`` and the ledger checks of a live ``bench`` command (docs/BENCHMARKS.md)."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Sequence
from typing import Any, Literal

import typer
from rich.table import Table

from engineering_team.bench import report as reporting
from engineering_team.bench.results import RunRecord
from engineering_team.bench.spend import SpendEntry, SpendLedger, now
from engineering_team.bench.tasks import BenchError
from engineering_team.cli.bench_options import SuiteOption, suite_path
from engineering_team.cli.context import fail, print_json
from engineering_team.cli.context import get as get_globals


def spend(ctx: typer.Context, suite: SuiteOption = None) -> None:
    """Show the release's live spend: allocated, spent and left per plan task."""

    g = get_globals(ctx)
    ledger = SpendLedger(suite_path(suite))
    try:
        budget, rows = ledger.budget(), ledger.rows()
        spent = ledger.spent()
    except BenchError as exc:
        fail(str(exc))
    if g.json:
        print_json(
            {"release": budget.release, "total_usd": budget.total_usd, "spent_usd": round(spent, 6),
             "remaining_usd": round(budget.total_usd - spent, 6), "allocations": rows}
        )  # fmt: skip
        return
    table = Table("Allocation", "Allocated", "Spent", "Left", "Commands", "Subscription", box=None)
    for row in rows:
        unknown = f" ({row['unknown_cost']} unknown)" if row["unknown_cost"] else ""
        table.add_row(
            str(row["task"]),
            f"${row['allocated_usd']:.2f}",
            f"${row['spent_usd']:.4f}",
            f"${row['remaining_usd']:.4f}",
            f"{row['commands']}{unknown}",
            str(row["subscription_runs"]),
        )
    console = g.console()
    console.print(table)
    console.print(
        f"Release {budget.release or '?'}: ${spent:.4f} of ${budget.total_usd:.2f} spent, "
        f"${budget.total_usd - spent:.4f} left. Unknown costs count as their estimate; "
        "subscription runs are not counted.",
        markup=False,
        soft_wrap=True,
    )


def actual_cost(records: Sequence[RunRecord]) -> float | None:
    """What the runs cost; ``None`` when a run that used a model has no price (unknown)."""

    total = 0.0
    for record in records:
        if record.cost_usd is not None:
            total += record.cost_usd
        elif record.tokens or record.model_calls:
            return None
    return round(total, 8)


def runs_since(records: Sequence[RunRecord], began: str) -> list[RunRecord]:
    """The runs started at or after ``began`` (ISO time, as ``result.json`` writes it): what this
    invocation spent, not the runs ``--resume`` kept from an earlier one."""

    return [record for record in records if record.started >= began]


def check_spend(
    ledger: SpendLedger, allocation: str | None, estimate: float | None, cap: float
) -> float:
    """Refuse a live batch the ledger cannot afford (exit 2); returns the estimate it charged."""

    if not allocation:
        fail(
            "A live run is charged to the release budget: pass --allocation TASK (a plan task in "
            f"{ledger.budget_path.name}, such as T25). See `bench spend`."
        )
    charged = cap if estimate is None else estimate  # an unknown estimate counts as the cap
    try:
        ledger.check(allocation, charged, cap)
    except BenchError as exc:
        fail(f"Refused: {exc}")
    return charged


def record_spend(
    ledger: SpendLedger,
    allocation: str,
    estimate: float,
    records: Sequence[RunRecord],
    batch: str,
    billing: Literal["api", "subscription"] = "api",
) -> SpendEntry:
    """Append one ledger line. A ``subscription`` line (runs a plan paid for) costs nothing: it
    only records that they happened, with their token usage."""

    if billing == "subscription":
        tokens = sum(r.tokens for r in records)
        entry = SpendEntry(
            date=now(), task=allocation, command=shlex.join(["engineering-team", *sys.argv[1:]]),
            estimated_usd=0.0, actual_usd=0.0, billing="subscription", batch=batch,
            note=f"{len(records)} run(s) on a plan, {tokens} tokens",
        )  # fmt: skip
        ledger.record(entry)
        return entry
    entry = SpendEntry(
        date=now(),
        task=allocation,
        command=shlex.join(["engineering-team", *sys.argv[1:]]),
        estimated_usd=round(estimate, 6),
        actual_usd=actual_cost(records),
        batch=batch,
        note=f"{len(records)} run(s), {reporting.money(actual_cost(records))}",
    )
    ledger.record(entry)
    return entry


def record_batch_spend(
    console: Any,
    ledger: SpendLedger,
    allocation: str,
    charged: float | None,
    records: Sequence[RunRecord],
    began: str,
    batch: str,
) -> None:
    """Write what this invocation spent into the ledger: one line for the money-billed runs
    (when any were charged) and one for the runs a plan paid for."""

    ran = runs_since(records, began)
    on_plan = [r for r in ran if r.billing == "subscription"]
    billed = [r for r in ran if r.billing not in ("subscription", "none")]
    if charged is not None:
        entry = record_spend(ledger, allocation, charged, billed, batch)
        console.print(
            f"Spend recorded in {ledger.ledger_path.name}: {allocation} "
            f"{reporting.money(entry.actual_usd)} (estimate ${entry.estimated_usd:.4f}).",
            markup=False,
            soft_wrap=True,
        )
    if on_plan:
        record_spend(ledger, allocation, 0.0, on_plan, batch, billing="subscription")
        console.print(
            f"{len(on_plan)} run(s) on a plan recorded in {ledger.ledger_path.name} (not charged).",
            markup=False,
            soft_wrap=True,
        )
