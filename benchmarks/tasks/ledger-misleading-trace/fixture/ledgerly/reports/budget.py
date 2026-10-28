"""Monthly budgets per expense category, and how the spending compares.

Budgets live in a small CSV file (``category,monthly``). A category over its budget, or within
``WARN_SHARE`` of it, is flagged.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ledgerly.core.accounts import Kind
from ledgerly.core.errors import LedgerError
from ledgerly.core.journal import Journal
from ledgerly.core.money import Money
from ledgerly.core.periods import Period
from ledgerly.fx.rates import RateTable
from ledgerly.reports.income import category_totals

WARN_SHARE = Decimal("0.9")


@dataclass
class BudgetLine:
    category: str
    budget: Money
    spent: Money

    @property
    def left(self) -> Money:
        return self.budget - self.spent

    @property
    def status(self) -> str:
        if self.spent.amount > self.budget.amount:
            return "over"
        if self.budget.amount and self.spent.amount >= self.budget.amount * WARN_SHARE:
            return "close"
        return "ok"


def load_budgets(path: Path, journal: Journal) -> dict[str, Decimal]:
    """``{category: monthly amount}``; every category must be an expense category."""

    budgets: dict[str, Decimal] = {}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for line, row in enumerate(csv.DictReader(handle), start=2):
                category = (row.get("category") or "").strip()
                if category not in journal.chart:
                    raise LedgerError(f"{path.name}:{line}: no category {category!r}")
                if journal.chart.get(category).kind is not Kind.EXPENSE:
                    raise LedgerError(f"{path.name}:{line}: {category} is not an expense category")
                try:
                    amount = Decimal((row.get("monthly") or "").strip())
                except InvalidOperation:
                    raise LedgerError(f"{path.name}:{line}: bad amount {row.get('monthly')!r}") from None
                if amount < 0:
                    raise LedgerError(f"{path.name}:{line}: a budget cannot be negative")
                budgets[category] = amount
    except OSError:
        raise LedgerError(f"cannot read the budget file {path}") from None
    return budgets


def compare(
    journal: Journal, period: Period, rates: RateTable, budgets: dict[str, Decimal]
) -> list[BudgetLine]:
    """Spending against budget for ``period``; a budget is per month, so it is scaled to the
    period's length (a quarter has three months of budget)."""

    months = max(1, round(period.days / 30.4))
    spent = category_totals(journal, period, rates)
    lines = []
    for category, monthly in sorted(budgets.items()):
        amount = -spent.get(category, (Money(Decimal("0"), rates.home), 0))[0].amount
        lines.append(
            BudgetLine(category, Money(monthly * months, rates.home), Money(amount, rates.home))
        )
    return lines


def render_budget(lines: list[BudgetLine], period: Period) -> str:
    out = [f"Budget, {period.label}", ""]
    for line in lines:
        flag = {"over": "  OVER", "close": "  close", "ok": ""}[line.status]
        out.append(f"{line.category:<16}{line.spent!s:>16} of {line.budget!s:>14}{flag}")
    if not lines:
        out.append("(no budgets)")
    return "\n".join(out) + "\n"
