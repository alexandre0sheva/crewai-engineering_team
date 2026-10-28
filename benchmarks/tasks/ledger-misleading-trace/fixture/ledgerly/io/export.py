"""Exporting the journal as CSV, for a spreadsheet or an accountant."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from ledgerly.core.periods import Period
from ledgerly.core.transactions import Transaction

COLUMNS = ("date", "account", "amount", "currency", "category", "counterparty", "description", "source")


def export_csv(transactions: Iterable[Transaction], period: Period | None = None) -> str:
    """The transactions (those in ``period``, if given) as CSV text with a header line."""

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for t in transactions:
        if period is not None and not period.contains(t.booked):
            continue
        writer.writerow(
            [
                t.booked.isoformat(),
                t.account,
                f"{t.amount:.2f}",
                t.currency,
                t.category or "",
                t.counterparty,
                t.description,
                t.source,
            ]
        )
    return out.getvalue()
