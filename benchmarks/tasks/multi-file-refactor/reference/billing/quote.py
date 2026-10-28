"""A quote: what a job would cost, before the customer accepts it."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from billing.pricing import discount_on, price_lines, tax_on, total_of


@dataclass(frozen=True)
class Quote:
    lines: list[tuple[str, Decimal]]
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


def build_quote(
    items: list[tuple[str, Decimal, int]], region: str, discount_percent: int = 0
) -> Quote:
    """``items`` are ``(description, unit price, quantity)``."""

    lines = price_lines(items)
    subtotal = total_of(lines)
    discount = discount_on(subtotal, discount_percent)
    taxable = subtotal - discount
    tax = tax_on(taxable, region)
    return Quote(lines, subtotal, discount, tax, taxable + tax)
