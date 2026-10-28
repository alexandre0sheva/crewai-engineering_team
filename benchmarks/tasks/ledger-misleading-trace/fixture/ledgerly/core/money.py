"""Money amounts: ``Decimal`` values with a currency, rounded to the cent only for display."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HOME_CURRENCY = "EUR"
KNOWN_CURRENCIES = frozenset({"EUR", "USD", "GBP", "CHF", "SEK", "DKK", "NOK", "PLN", "JPY"})
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY"})


def check_currency(code: str) -> str:
    code = code.strip().upper()
    if code not in KNOWN_CURRENCIES:
        raise ValueError(f"unknown currency {code!r}")
    return code


def round_cents(amount: Decimal, *, bankers: bool = False) -> Decimal:
    """Round to the cent: half up for display, half even (``bankers``) for interest."""

    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN if bankers else ROUND_HALF_UP)


@dataclass(frozen=True, order=True)
class Money:
    amount: Decimal
    currency: str = HOME_CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money needs a Decimal amount, not {type(self.amount).__name__}")
        object.__setattr__(self, "currency", check_currency(self.currency))

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.amount * Decimal(factor), self.currency)

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise ValueError(f"cannot combine {self.currency} with {other.currency}")

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def rounded(self) -> Money:
        places = Decimal("1") if self.currency in ZERO_DECIMAL_CURRENCIES else CENT
        return Money(self.amount.quantize(places, rounding=ROUND_HALF_UP), self.currency)

    def __str__(self) -> str:
        value = self.rounded().amount
        if self.currency in ZERO_DECIMAL_CURRENCIES:
            return f"{value:,.0f} {self.currency}"
        return f"{value:,.2f} {self.currency}"


def total(values: list[Money], currency: str = HOME_CURRENCY) -> Money:
    """The sum of ``values`` (all in ``currency``); zero for an empty list."""

    result = Money(Decimal("0"), currency)
    for value in values:
        result = result + value
    return result
