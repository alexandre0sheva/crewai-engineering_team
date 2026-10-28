"""``python -m parcels VALUE WEIGHT_KG COUNTRY``: the shipping cost and the delivery date."""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation

from parcels.delivery import delivery_date
from parcels.quote import CannotShip, shipping_cost


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m parcels VALUE WEIGHT_KG COUNTRY", file=sys.stderr)
        return 2
    try:
        value, weight = Decimal(argv[0]), Decimal(argv[1])
        cost = shipping_cost(value, weight, argv[2])
    except (InvalidOperation, CannotShip, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"shipping: {cost}")
    print(f"arrives: {delivery_date(datetime.now(), argv[2]).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
