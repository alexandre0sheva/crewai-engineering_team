"""Exchange rates: units of the home currency per unit of a foreign one, per day.

Rates come from a CSV file (``date,currency,rate``), typically the ECB's reference rates. A rate
for a day without one (a weekend, a holiday) is the most recent earlier rate, at most
``MAX_STALE_DAYS`` old.
"""

from __future__ import annotations

import bisect
import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ledgerly.core.errors import RateError
from ledgerly.core.money import HOME_CURRENCY, check_currency

MAX_STALE_DAYS = 7


class RateTable:
    def __init__(self, home: str = HOME_CURRENCY) -> None:
        self.home = check_currency(home)
        self._days: dict[str, list[date]] = defaultdict(list)
        self._rates: dict[str, list[Decimal]] = defaultdict(list)

    def add(self, currency: str, day: date, rate: Decimal) -> None:
        currency = check_currency(currency)
        if currency == self.home:
            raise RateError(f"{currency} is the home currency; it has no rate")
        if rate <= 0:
            raise RateError(f"a rate must be positive, not {rate}")
        days, rates = self._days[currency], self._rates[currency]
        at = bisect.bisect_left(days, day)
        if at < len(days) and days[at] == day:
            rates[at] = rate
        else:
            days.insert(at, day)
            rates.insert(at, rate)

    def rate(self, currency: str, day: date) -> Decimal:
        """Home-currency units per unit of ``currency`` on ``day``."""

        currency = check_currency(currency)
        if currency == self.home:
            return Decimal(1)
        days = self._days.get(currency)
        if not days:
            raise RateError(f"no exchange rates for {currency}")
        at = bisect.bisect_right(days, day) - 1
        if at < 0:
            raise RateError(f"no {currency} rate on or before {day}")
        if day - days[at] > timedelta(days=MAX_STALE_DAYS):
            raise RateError(f"the latest {currency} rate before {day} is from {days[at]}")
        return self._rates[currency][at]

    def currencies(self) -> list[str]:
        return sorted(self._days)

    def __len__(self) -> int:
        return sum(len(days) for days in self._days.values())


def load_rates(path: Path, home: str = HOME_CURRENCY) -> RateTable:
    table = RateTable(home)
    if not path.exists():
        return table
    with path.open(newline="", encoding="utf-8") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                table.add(
                    row["currency"], date.fromisoformat(row["date"]), Decimal(row["rate"].strip())
                )
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise RateError(f"{path.name}:{line}: {exc}") from None
    return table
