"""Plain-text rendering of reports, for the terminal."""

from __future__ import annotations

from decimal import Decimal

from ledgerly.core.money import Money
from ledgerly.reports.balance import Balance, net_worth
from ledgerly.reports.income import CategoryLine, IncomeStatement
from ledgerly.util.text import shorten

NAME_WIDTH = 24
AMOUNT_WIDTH = 16


def amount_cell(value: Money) -> str:
    return str(value).rjust(AMOUNT_WIDTH)


def percent(value: Decimal | None) -> str:
    if value is None:
        return "     –"
    return f"{value * 100:+5.0f}%"


def _line(line: CategoryLine, compare: bool) -> str:
    text = f"  {shorten(line.name, NAME_WIDTH):<{NAME_WIDTH}}{amount_cell(line.amount)}"
    text += f"  ({line.count})"
    if compare:
        text += f"  {percent(line.change)}"
    return text


def render_income(statement: IncomeStatement, *, compare: bool = False) -> str:
    out = [f"Income and expenses, {statement.period.label}", ""]
    out.append("Income")
    out += [_line(line, compare) for line in statement.income] or ["  (none)"]
    out.append(f"  {'Total income':<{NAME_WIDTH}}{amount_cell(statement.total_income)}")
    out += ["", "Expenses"]
    out += [_line(line, compare) for line in statement.expenses] or ["  (none)"]
    out.append(f"  {'Total expenses':<{NAME_WIDTH}}{amount_cell(statement.total_expenses)}")
    out += ["", f"  {'Net':<{NAME_WIDTH}}{amount_cell(statement.net)}"]
    if statement.savings_rate is not None:
        out.append(f"  {'Savings rate':<{NAME_WIDTH}}{statement.savings_rate * 100:>14.1f} %")
    return "\n".join(out) + "\n"


def render_balances(rows: list[Balance], currency: str) -> str:
    out = []
    for row in rows:
        own = "" if row.own.currency == currency else f"  ({row.own})"
        out.append(f"{shorten(row.name, NAME_WIDTH):<{NAME_WIDTH}}{amount_cell(row.home)}{own}")
    out.append(f"{'Net worth':<{NAME_WIDTH}}{amount_cell(net_worth(rows, currency))}")
    return "\n".join(out) + "\n"
