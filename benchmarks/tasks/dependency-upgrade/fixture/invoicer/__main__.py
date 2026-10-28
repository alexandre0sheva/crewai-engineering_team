"""``python -m invoicer "Design=1,200.00" "Hosting=49.99" ...``: the invoice lines and total."""

from __future__ import annotations

import sys

from invoicer.totals import InvoiceError, parse_line, render


def main(argv: list[str]) -> int:
    if not argv:
        print('usage: python -m invoicer "DESCRIPTION=AMOUNT" ...', file=sys.stderr)
        return 2
    try:
        lines = [parse_line(arg) for arg in argv]
    except InvoiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for row in render(lines):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
