"""An invoice: what the customer owes for an accepted job, with a due date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATES = {"DE": Decimal("0.19"), "FR": Decimal("0.20"), "NL": Decimal("0.21")}
PAYMENT_DAYS = 14


@dataclass(frozen=True)
class Invoice:
    number: str
    issued: date
    due: date
    lines: list[tuple[str, Decimal]]
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


def build_invoice(
    number: str,
    issued: date,
    items: list[tuple[str, Decimal, int]],
    region: str,
    discount_percent: int = 0,
) -> Invoice:
    """``items`` are ``(description, unit price, quantity)``; the invoice is due in 14 days."""

    if not number.startswith("INV-"):
        raise ValueError("invoice numbers start with INV-")
    lines = []
    for description, unit_price, quantity in items:
        if quantity <= 0:
            raise ValueError(f"quantity of {description!r} must be positive")
        lines.append((description, (unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)))
    subtotal = sum((amount for _, amount in lines), Decimal("0.00"))
    if discount_percent < 0 or discount_percent > 50:
        raise ValueError("a discount is between 0 and 50 percent")
    discount = (subtotal * Decimal(discount_percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    if region not in TAX_RATES:
        raise ValueError(f"no tax rate for region {region!r}")
    tax = ((subtotal - discount) * TAX_RATES[region]).quantize(CENT, rounding=ROUND_HALF_UP)
    total = subtotal - discount + tax
    return Invoice(
        number, issued, issued + timedelta(days=PAYMENT_DAYS), lines, subtotal, discount, tax, total
    )
