"""moneyfmt 1.4.0: format and parse money amounts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__version__ = "1.4.0"
__all__ = ["CURRENCIES", "MoneyError", "format_money", "parse_money"]

CURRENCIES = {"EUR": "€", "USD": "$", "GBP": "£"}


class MoneyError(ValueError):
    """An amount that cannot be read, or a currency that is not known."""


def format_money(amount, currency):
    """``format_money(1234.5, "EUR")`` -> ``"€1,234.50"`` (rounded half up to the cent)."""

    if currency not in CURRENCIES:
        raise MoneyError(f"unknown currency {currency!r}")
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return f"{sign}{CURRENCIES[currency]}{abs(value):,.2f}"


def parse_money(text):
    """``parse_money("1,234.50")`` -> ``1234.5`` (a float); currency symbols are ignored."""

    cleaned = text.strip()
    for symbol in CURRENCIES.values():
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "")
    try:
        return float(Decimal(cleaned))
    except InvalidOperation:
        raise MoneyError(f"cannot read amount {text!r}") from None
