"""``python -m billing quote|receipt REGION [--discount N] [--paid AMOUNT] ITEM...``

An item is ``description:unit_price:quantity``, e.g. ``"Oil change:49.90:1"``.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from billing.quote import build_quote
from billing.receipt import build_receipt


def parse_item(text: str) -> tuple[str, Decimal, int]:
    try:
        description, price, quantity = text.rsplit(":", 2)
        return description, Decimal(price), int(quantity)
    except (ValueError, InvalidOperation):
        raise argparse.ArgumentTypeError(f"bad item {text!r}: use description:price:qty") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="billing")
    parser.add_argument("kind", choices=["quote", "receipt"])
    parser.add_argument("region")
    parser.add_argument("items", nargs="+", type=parse_item)
    parser.add_argument("--discount", type=int, default=0)
    parser.add_argument("--paid", type=Decimal)
    args = parser.parse_args(argv)
    try:
        if args.kind == "quote":
            quote = build_quote(args.items, args.region, args.discount)
            for description, amount in quote.lines:
                print(f"{description:<30}{amount:>10}")
            print(f"{'Subtotal':<30}{quote.subtotal:>10}")
            print(f"{'Discount':<30}{-quote.discount:>10}")
            print(f"{'Tax':<30}{quote.tax:>10}")
            print(f"{'Total':<30}{quote.total:>10}")
        else:
            paid = args.paid if args.paid is not None else Decimal("0")
            receipt = build_receipt(args.items, args.region, paid, args.discount)
            for description, amount in receipt.lines:
                print(f"{description:<30}{amount:>10}")
            print(f"{'Total (incl. tax ' + str(receipt.tax_included) + ')':<30}{receipt.total:>10}")
            print(f"{'Paid':<30}{receipt.paid:>10}")
            print(f"{'Change':<30}{receipt.change:>10}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
