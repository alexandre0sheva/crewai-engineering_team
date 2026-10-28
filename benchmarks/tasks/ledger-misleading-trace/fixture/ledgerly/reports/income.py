"""Income and expenses per category for a period, in the home currency."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ledgerly.core.accounts import Kind
from ledgerly.core.journal import Journal
from ledgerly.core.money import Money, total
from ledgerly.core.periods import Period
from ledgerly.fx.convert import convert_all
from ledgerly.fx.rates import RateTable


@dataclass
class CategoryLine:
    category: str
    name: str
    amount: Money
    count: int
    previous: Money | None = None

    @property
    def change(self) -> Decimal | None:
        """Change against the previous period, as a fraction (``None`` when it had nothing)."""

        if self.previous is None or self.previous.is_zero:
            return None
        return (self.amount.amount - self.previous.amount) / abs(self.previous.amount)


@dataclass
class IncomeStatement:
    period: Period
    income: list[CategoryLine] = field(default_factory=list)
    expenses: list[CategoryLine] = field(default_factory=list)
    currency: str = "EUR"

    @property
    def total_income(self) -> Money:
        return total([line.amount for line in self.income], self.currency)

    @property
    def total_expenses(self) -> Money:
        return total([line.amount for line in self.expenses], self.currency)

    @property
    def net(self) -> Money:
        return self.total_income + self.total_expenses

    @property
    def savings_rate(self) -> Decimal | None:
        income = self.total_income.amount
        return None if income <= 0 else self.net.amount / income


def category_totals(
    journal: Journal, period: Period, rates: RateTable
) -> dict[str, tuple[Money, int]]:
    """Per category: the total in the home currency and the number of transactions."""

    totals: dict[str, tuple[Money, int]] = {}
    for category, transactions in journal.by_category(period).items():
        converted = convert_all(transactions, rates)
        totals[category] = (total(converted, rates.home), len(transactions))
    return totals


def income_statement(
    journal: Journal, period: Period, rates: RateTable, *, compare: bool = False
) -> IncomeStatement:
    current = category_totals(journal, period, rates)
    previous = category_totals(journal, period.previous(), rates) if compare else {}
    statement = IncomeStatement(period, currency=rates.home)
    for category, (amount, count) in sorted(current.items()):
        account = journal.chart.get(category)
        before = previous.get(category, (None, 0))[0] if compare else None
        line = CategoryLine(category, account.name, amount, count, before)
        if account.kind is Kind.INCOME:
            statement.income.append(line)
        else:
            statement.expenses.append(line)
    statement.income.sort(key=lambda line: -line.amount.amount)
    statement.expenses.sort(key=lambda line: line.amount.amount)
    return statement
