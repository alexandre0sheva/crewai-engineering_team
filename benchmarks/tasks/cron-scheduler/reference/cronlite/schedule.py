"""Parsing and next-run calculation."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 6))
PART = re.compile(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?")
HORIZON_DAYS = 366 * 8


def parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        match = PART.fullmatch(part)
        if not match:
            raise ValueError(f"invalid {name} field: {text!r}")
        span, step = match.group(1), int(match.group(2) or 1)
        if step == 0:
            raise ValueError(f"step 0 in the {name} field")
        if match.group(2) and span != "*" and "-" not in span:
            raise ValueError(f"a step needs * or a range in the {name} field: {part!r}")
        if span == "*":
            start, end = low, high
        else:
            start, _, stop = span.partition("-")
            start, end = int(start), int(stop or start)
        if not (low <= start <= end <= high):
            raise ValueError(f"{name} value out of range {low}-{high}: {part!r}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Schedule:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    def dom_ok(self, day: date) -> bool:
        return day.day in self.days

    def dow_ok(self, day: date) -> bool:
        return (day.weekday() + 1) % 7 in self.weekdays

    def day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return self.dow_ok(day)
        if self.any_weekday:
            return self.dom_ok(day)
        return self.dom_ok(day) or self.dow_ok(day)


def parse(expression: str) -> Schedule:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(f"a cron expression has 5 fields, got {len(parts)}")
    sets = [parse_field(text, *spec) for text, spec in zip(parts, FIELDS, strict=True)]
    return Schedule(*sets, any_day=parts[2] == "*", any_weekday=parts[4] == "*")


def next_run(expression: str, after: datetime) -> datetime:
    schedule = parse(expression)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    for _ in range(HORIZON_DAYS):
        if schedule.day_matches(day):
            for hour in sorted(schedule.hours):
                for minute in sorted(schedule.minutes):
                    moment = datetime.combine(day, time(hour, minute))
                    if moment >= start:
                        return moment
        day += timedelta(days=1)
    raise ValueError(f"{expression!r} does not fire within 8 years")
