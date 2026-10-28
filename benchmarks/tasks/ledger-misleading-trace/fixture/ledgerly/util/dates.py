"""Dates as banks write them."""

from __future__ import annotations

import re
from datetime import date, datetime

FORMATS = ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


def parse_bank_date(text: str, *, formats: tuple[str, ...] = FORMATS) -> date:
    """The first format in ``formats`` that reads ``text``; ``ValueError`` if none does."""

    cleaned = text.strip()
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"cannot read the date {text!r}")


def month_key(day: date) -> str:
    return f"{day:%Y-%m}"


def months_between(first: date, last: date) -> list[str]:
    """Every ``YYYY-MM`` from ``first`` to ``last`` inclusive."""

    if last < first:
        return []
    keys = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return keys


def looks_like_date(text: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-?\d{2}-?\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4}", text.strip()))
