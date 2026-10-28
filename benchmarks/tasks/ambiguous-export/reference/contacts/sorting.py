"""How names are ordered.

"Sorted by name" could mean by first name or by last name. A printed phone list is usually ordered
by last name, so that is the assumption here: names are sorted by their last word (the last name),
case-insensitively, and names with the same last name by the whole name.
"""

from __future__ import annotations


def last_name_key(name: str) -> tuple[str, str]:
    words = name.split()
    last = words[-1] if words else ""
    return (last.casefold(), name.casefold())


def sort_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(rows, key=lambda row: last_name_key(row.get("name", "")))
