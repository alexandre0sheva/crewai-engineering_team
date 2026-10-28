"""A receipt: a paid counter sale. Prices include tax, so the tax is worked out backwards."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from billing.pricing import discount_on, price_lines, tax_included_in, total_of


@dataclass(frozen=True)
class Receipt:
    lines: list[tuple[str, Decimal]]
    total: Decimal
    discount: Decimal
    tax_included: Decimal
    paid: Decimal
    change: Decimal


def build_receipt(
    items: list[tuple[str, Decimal, int]],
    region: str,
    paid: Decimal,
    discount_percent: int = 0,
) -> Receipt:
    """``items`` are ``(description, gross unit price, quantity)``; ``paid`` is what was handed over."""

    lines = price_lines(items)
    gross = total_of(lines)
    discount = discount_on(gross, discount_percent)
    total = gross - discount
    tax_included = tax_included_in(total, region)
    if paid < total:
        raise ValueError(f"paid {paid} is less than the total {total}")
    return Receipt(lines, total, discount, tax_included, paid, paid - total)
