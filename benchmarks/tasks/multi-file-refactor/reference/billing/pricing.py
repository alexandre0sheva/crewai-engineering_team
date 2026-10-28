"""The money rules quotes, invoices and receipts share: cents, discounts and tax."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATES = {"DE": Decimal("0.19"), "FR": Decimal("0.20"), "NL": Decimal("0.21")}
MAX_DISCOUNT_PERCENT = 50


def to_cents(amount: Decimal) -> Decimal:
    """Round to the cent, half up."""

    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def price_lines(items: list[tuple[str, Decimal, int]]) -> list[tuple[str, Decimal]]:
    """``(description, unit price, quantity)`` -> ``(description, line amount)``."""

    lines = []
    for description, unit_price, quantity in items:
        if quantity <= 0:
            raise ValueError(f"quantity of {description!r} must be positive")
        lines.append((description, to_cents(unit_price * quantity)))
    return lines


def total_of(lines: list[tuple[str, Decimal]]) -> Decimal:
    return sum((amount for _, amount in lines), Decimal("0.00"))


def discount_on(amount: Decimal, percent: int) -> Decimal:
    """The discount for ``percent`` (0 to 50) of ``amount``, in cents."""

    if not 0 <= percent <= MAX_DISCOUNT_PERCENT:
        raise ValueError("a discount is between 0 and 50 percent")
    return to_cents(amount * Decimal(percent) / 100)


def tax_rate(region: str) -> Decimal:
    if region not in TAX_RATES:
        raise ValueError(f"no tax rate for region {region!r}")
    return TAX_RATES[region]


def tax_on(net: Decimal, region: str) -> Decimal:
    """Tax added on top of a net amount."""

    return to_cents(net * tax_rate(region))


def tax_included_in(gross: Decimal, region: str) -> Decimal:
    """The tax already inside a gross amount."""

    return to_cents(gross - gross / (1 + tax_rate(region)))
