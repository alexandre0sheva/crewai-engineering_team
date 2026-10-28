# moneyfmt changelog

## 2.0.0

Breaking changes:

- `parse_money` returns a `Decimal` instead of a `float`.
- `format_money` takes a `Decimal` only (a `float` or `int` raises `TypeError`), and `currency` is
  keyword-only: `format_money(amount, currency="EUR")`.
- `MoneyError` is renamed `MoneyFormatError`. The old name is gone.

Added:

- `format_money(..., grouping=False)` leaves out the thousands separators.
- `CHF`.
- `parse_money` rejects `NaN` and `Infinity`.

## 1.4.0

- `GBP`.
