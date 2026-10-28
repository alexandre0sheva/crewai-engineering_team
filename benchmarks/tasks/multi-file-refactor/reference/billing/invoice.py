"""An invoice: what the customer owes for an accepted job, with a due date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from billing.pricing import discount_on, price_lines, tax_on, total_of

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
    lines = price_lines(items)
    subtotal = total_of(lines)
    discount = discount_on(subtotal, discount_percent)
    tax = tax_on(subtotal - discount, region)
    total = subtotal - discount + tax
    return Invoice(
        number, issued, issued + timedelta(days=PAYMENT_DAYS), lines, subtotal, discount, tax, total
    )
