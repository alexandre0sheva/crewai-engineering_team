"""Invoice lines and their total."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from moneyfmt import MoneyFormatError, format_money, parse_money

CURRENCY = "EUR"


class InvoiceError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    description: str
    amount: Decimal


def parse_line(text: str) -> Line:
    """``"Design=1,200.00"`` -> a line."""

    description, sep, amount = text.partition("=")
    if not sep or not description.strip():
        raise InvoiceError(f"a line is DESCRIPTION=AMOUNT, not {text!r}")
    try:
        return Line(description.strip(), parse_money(amount))
    except MoneyFormatError as exc:
        raise InvoiceError(str(exc)) from None


def total(lines: list[Line]) -> Decimal:
    return sum((line.amount for line in lines), Decimal("0"))


def render(lines: list[Line]) -> list[str]:
    rows = [
        f"{line.description:<24}{format_money(line.amount, currency=CURRENCY):>14}"
        for line in lines
    ]
    rows.append(f"{'Total':<24}{format_money(total(lines), currency=CURRENCY):>14}")
    return rows
