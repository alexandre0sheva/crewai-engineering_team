"""``python -m contacts sort INPUT.csv OUTPUT.csv``"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from contacts.sorting import sort_rows

USAGE = "usage: python -m contacts sort INPUT.csv OUTPUT.csv"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "sort":
        print(USAGE, file=sys.stderr)
        return 2
    source, target = Path(argv[1]), Path(argv[2])
    try:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
    except OSError:
        print(f"error: cannot read {source}", file=sys.stderr)
        return 2
    if "name" not in fields:
        print(f"error: {source} has no name column", file=sys.stderr)
        return 2
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sort_rows(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
