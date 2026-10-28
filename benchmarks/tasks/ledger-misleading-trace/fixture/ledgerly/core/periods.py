"""Reporting periods: a month, a quarter, a year, or any inclusive range of days."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ledgerly.core.errors import LedgerError


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    label: str

    def __post_init__(self) -> None:
        if self.last < self.first:
            raise LedgerError(f"period {self.label} ends before it starts")

    def contains(self, day: date) -> bool:
        return self.first <= day <= self.last

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def previous(self) -> Period:
        if re.fullmatch(r"\d{4}-\d{2}", self.label):
            return month(self.first - timedelta(days=1))
        length = self.days
        last = self.first - timedelta(days=1)
        return Period(last - timedelta(days=length - 1), last, f"before {self.label}")


def month(day: date) -> Period:
    last_day = calendar.monthrange(day.year, day.month)[1]
    return Period(
        date(day.year, day.month, 1), date(day.year, day.month, last_day), f"{day:%Y-%m}"
    )


def quarter(year: int, number: int) -> Period:
    if number not in (1, 2, 3, 4):
        raise LedgerError("a quarter is 1, 2, 3 or 4")
    first = date(year, 3 * number - 2, 1)
    last = month(date(year, 3 * number, 1)).last
    return Period(first, last, f"{year}-Q{number}")


def year(value: int) -> Period:
    return Period(date(value, 1, 1), date(value, 12, 31), str(value))


def parse_period(text: str) -> Period:
    """``2026-03`` (a month), ``2026-Q1``, ``2026``, or ``2026-01-15..2026-02-14``."""

    text = text.strip()
    if found := re.fullmatch(r"(\d{4})-(\d{2})", text):
        number = int(found[2])
        if not 1 <= number <= 12:
            raise LedgerError(f"no month {number} in {text!r}")
        return month(date(int(found[1]), number, 1))
    if found := re.fullmatch(r"(\d{4})-[Qq]([1-4])", text):
        return quarter(int(found[1]), int(found[2]))
    if re.fullmatch(r"\d{4}", text):
        return year(int(text))
    if found := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", text):
        try:
            return Period(date.fromisoformat(found[1]), date.fromisoformat(found[2]), text)
        except ValueError as exc:
            raise LedgerError(f"bad date in {text!r}: {exc}") from None
    raise LedgerError(f"cannot read the period {text!r}: use 2026-03, 2026-Q1, 2026 or A..B")
