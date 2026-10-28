"""A quote: what a job would cost, before the customer accepts it."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATES = {"DE": Decimal("0.19"), "FR": Decimal("0.20"), "NL": Decimal("0.21")}


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

    lines = []
    for description, unit_price, quantity in items:
        if quantity <= 0:
            raise ValueError(f"quantity of {description!r} must be positive")
        amount = (unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
        lines.append((description, amount))
    subtotal = sum((amount for _, amount in lines), Decimal("0.00"))
    if not 0 <= discount_percent <= 50:
        raise ValueError("a discount is between 0 and 50 percent")
    discount = (subtotal * Decimal(discount_percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    taxable = subtotal - discount
    if region not in TAX_RATES:
        raise ValueError(f"no tax rate for region {region!r}")
    tax = (taxable * TAX_RATES[region]).quantize(CENT, rounding=ROUND_HALF_UP)
    return Quote(lines, subtotal, discount, tax, taxable + tax)
