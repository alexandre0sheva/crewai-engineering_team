"""A receipt: a paid counter sale. Prices include tax, so the tax is worked out backwards."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATES = {"DE": Decimal("0.19"), "FR": Decimal("0.20"), "NL": Decimal("0.21")}


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

    lines = []
    for description, unit_price, quantity in items:
        if quantity <= 0:
            raise ValueError(f"quantity of {description!r} must be positive")
        lines.append((description, (unit_price * quantity).quantize(CENT, ROUND_HALF_UP)))
    gross = sum((amount for _, amount in lines), Decimal("0.00"))
    if not (0 <= discount_percent <= 50):
        raise ValueError("a discount is between 0 and 50 percent")
    discount = (gross * Decimal(discount_percent) / 100).quantize(CENT, ROUND_HALF_UP)
    total = gross - discount
    rate = TAX_RATES.get(region)
    if rate is None:
        raise ValueError(f"no tax rate for region {region!r}")
    tax_included = (total - total / (1 + rate)).quantize(CENT, ROUND_HALF_UP)
    if paid < total:
        raise ValueError(f"paid {paid} is less than the total {total}")
    return Receipt(lines, total, discount, tax_included, paid, paid - total)
