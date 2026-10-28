"""Converting transaction amounts to the home currency for reports."""

from __future__ import annotations

from decimal import Decimal

from ledgerly.core.money import Money, round_cents
from ledgerly.core.transactions import Transaction
from ledgerly.fx.rates import RateTable


def to_home(amount: Decimal, currency: str, table: RateTable, day) -> Decimal:  # noqa: ANN001
    """``amount`` of ``currency`` in the home currency on ``day``, rounded to the cent."""

    rate = table.rate(currency, day)
    return round_cents(amount * rate)


def transaction_in_home(transaction: Transaction, table: RateTable) -> Money:
    value = to_home(transaction.amount, transaction.currency, table, transaction.booked)
    return Money(value, table.home)


def convert_all(transactions: list[Transaction], table: RateTable) -> list[Money]:
    return [transaction_in_home(t, table) for t in transactions]
