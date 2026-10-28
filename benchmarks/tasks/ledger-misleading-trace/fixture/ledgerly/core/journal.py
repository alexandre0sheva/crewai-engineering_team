"""The journal: every imported transaction, de-duplicated, in booking order."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import date

from ledgerly.core.accounts import Chart, Kind
from ledgerly.core.errors import LedgerError
from ledgerly.core.periods import Period
from ledgerly.core.transactions import Transaction


class Journal:
    def __init__(self, chart: Chart | None = None) -> None:
        self.chart = chart or Chart()
        self._entries: list[Transaction] = []
        self._seen: set[str] = set()

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(sorted(self._entries, key=lambda t: (t.booked, t.account, t.source)))

    def add(self, transaction: Transaction) -> bool:
        """Add a transaction; ``False`` (and nothing added) when it is already in the journal."""

        account = self.chart.get(transaction.account)
        if account.kind.is_category:
            raise LedgerError(f"{account.id} is a category, not an account money is kept in")
        if account.closed:
            raise LedgerError(f"account {account.id} is closed")
        if transaction.category is not None and transaction.category not in self.chart:
            raise LedgerError(f"no category {transaction.category!r}")
        if transaction.fingerprint in self._seen:
            return False
        self._seen.add(transaction.fingerprint)
        self._entries.append(transaction)
        return True

    def extend(self, transactions: Iterable[Transaction]) -> tuple[int, int]:
        """Add many; returns ``(added, duplicates)``."""

        added = duplicates = 0
        for transaction in transactions:
            if self.add(transaction):
                added += 1
            else:
                duplicates += 1
        return added, duplicates

    def replace_all(self, transactions: Iterable[Transaction]) -> None:
        self._entries, self._seen = [], set()
        self.extend(transactions)

    def in_period(self, period: Period) -> list[Transaction]:
        return [t for t in self if period.contains(t.booked)]

    def for_account(self, account_id: str) -> list[Transaction]:
        self.chart.get(account_id)
        return [t for t in self if t.account == account_id]

    def first_and_last(self) -> tuple[date, date] | None:
        dates = [t.booked for t in self._entries]
        return (min(dates), max(dates)) if dates else None

    def by_category(self, period: Period) -> dict[str, list[Transaction]]:
        grouped: dict[str, list[Transaction]] = defaultdict(list)
        for transaction in self.in_period(period):
            grouped[transaction.category or "uncategorised"].append(transaction)
        return dict(grouped)

    def money_accounts(self) -> list[str]:
        kinds = (Kind.ASSET, Kind.LIABILITY)
        return [a.id for a in self.chart.all() if a.kind in kinds]
