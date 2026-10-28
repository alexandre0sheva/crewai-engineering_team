"""moneyfmt 2.0.0: format and parse money amounts, as Decimal only."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__version__ = "2.0.0"
__all__ = ["CURRENCIES", "MoneyFormatError", "format_money", "parse_money"]

CURRENCIES = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF "}


class MoneyFormatError(ValueError):
    """An amount that cannot be read, or a currency that is not known."""


def format_money(amount: Decimal, *, currency: str, grouping: bool = True) -> str:
    """``format_money(Decimal("1234.5"), currency="EUR")`` -> ``"€1,234.50"``."""

    if not isinstance(amount, Decimal):
        raise TypeError(f"format_money takes a Decimal, not {type(amount).__name__}")
    if currency not in CURRENCIES:
        raise MoneyFormatError(f"unknown currency {currency!r}")
    value = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    number = f"{abs(value):,.2f}" if grouping else f"{abs(value):.2f}"
    return f"{sign}{CURRENCIES[currency]}{number}"


def parse_money(text: str) -> Decimal:
    """``parse_money("1,234.50")`` -> ``Decimal("1234.50")``; currency symbols are ignored."""

    cleaned = text.strip()
    for symbol in CURRENCIES.values():
        cleaned = cleaned.replace(symbol.strip(), "")
    cleaned = cleaned.replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise MoneyFormatError(f"cannot read amount {text!r}") from None
    if not value.is_finite():
        raise MoneyFormatError(f"cannot read amount {text!r}")
    return value
