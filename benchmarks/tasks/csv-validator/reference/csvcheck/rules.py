"""The per-value rules."""

import re
from datetime import date


def parse(kind: str, value: str):
    """The typed value, or raise ValueError saying what is wrong."""

    if kind == "int":
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"not an int: {value!r}") from None
    if kind == "float":
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"not a float: {value!r}") from None
    if kind == "date":
        try:
            return date.fromisoformat(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else 1 / 0
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"not a date (YYYY-MM-DD): {value!r}") from None
    if kind == "bool":
        if value not in ("true", "false"):
            raise ValueError(f"not a bool (true or false): {value!r}")
        return value == "true"
    return value


def check_value(column: dict, value: str) -> list[str]:
    """Every problem with a non-empty value."""

    try:
        number = parse(column["type"], value)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if column["type"] in ("int", "float"):
        low, high = column.get("min"), column.get("max")
        if low is not None and number < low:
            problems.append(f"{value} is below the minimum {low}")
        if high is not None and number > high:
            problems.append(f"{value} is above the maximum {high}")
    if "pattern" in column and not re.fullmatch(column["pattern"], value):
        problems.append(f"{value!r} does not match {column['pattern']!r}")
    if "allowed" in column and value not in column["allowed"]:
        problems.append(f"{value!r} is not one of {column['allowed']}")
    return problems
