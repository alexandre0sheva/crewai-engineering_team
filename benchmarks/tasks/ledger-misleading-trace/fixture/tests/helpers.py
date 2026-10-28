"""Shared test data."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from ledgerly.core.accounts import Account, Chart, Kind
from ledgerly.core.journal import Journal
from ledgerly.core.transactions import Transaction


def journal() -> Journal:
    chart = Chart()
    chart.add(Account("checking", "Checking", Kind.ASSET, iban="NL91ABNA0417164300"))
    chart.add(Account("card", "Credit card", Kind.LIABILITY))
    return Journal(chart)


def txn(amount: str, day: date = date(2026, 3, 2), **fields: object) -> Transaction:
    values: dict[str, object] = {
        "account": "checking",
        "booked": day,
        "amount": Decimal(amount),
        "currency": "EUR",
        "description": "test",
    }
    values.update(fields)
    return Transaction(**values)  # type: ignore[arg-type]


def write(text: str, name: str = "statement.csv") -> Path:
    directory = Path(tempfile.mkdtemp(prefix="ledgerly-test-"))
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path
