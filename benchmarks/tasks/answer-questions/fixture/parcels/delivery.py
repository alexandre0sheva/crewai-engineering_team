"""When an order arrives."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from parcels.settings import HANDLING_DAYS

TRANSIT_DAYS = {"NL": 1, "BE": 2, "DE": 2, "LU": 2}
DEFAULT_TRANSIT_DAYS = 4
CUTOFF_HOUR = 15  # orders placed at or after 15:00 are handled from the next day


def add_working_days(start: date, days: int) -> date:
    """``start`` plus ``days`` working days: Saturdays and Sundays are skipped (holidays are not)."""

    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


def delivery_date(ordered: datetime, country: str) -> date:
    start = ordered.date()
    if ordered.hour >= CUTOFF_HOUR:
        start += timedelta(days=1)
    transit = TRANSIT_DAYS.get(country.upper(), DEFAULT_TRANSIT_DAYS)
    return add_working_days(start, HANDLING_DAYS + transit)
