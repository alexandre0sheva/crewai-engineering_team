"""The bank statement formats ledgerly reads, one reader per bank.

Every reader takes the rows of a CSV file (as dictionaries, with the 1-based file line number) and
yields ``Transaction`` objects for one account. Formats:

- ``generic``: ``date,amount,currency,description,counterparty,iban`` with a decimal point and a
  sign (``-12.50``, ``+1250.00``).
- ``ing``: ING's export: ``Datum`` (YYYYMMDD), ``Naam / Omschrijving``, ``Tegenrekening``,
  ``Af Bij`` (``Af``: money out, ``Bij``: money in), ``Bedrag (EUR)`` (always positive, decimal
  comma), ``Mededelingen``.
- ``rabo``: Rabobank's export: ``Datum`` (YYYY-MM-DD), ``Bedrag`` (signed, decimal comma, thousands
  dots: ``-12,50``, ``+1.250,00``), ``Munt``, ``Naam tegenpartij``, ``Tegenrekening IBAN/BBAN``,
  ``Omschrijving-1``. Balance lines (``Saldo ...``) carry no amount and are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from decimal import Decimal, InvalidOperation

from ledgerly.core.errors import ImportError_
from ledgerly.core.transactions import Transaction
from ledgerly.util.dates import parse_bank_date
from ledgerly.util.text import squash, strip_reference

Row = tuple[int, dict[str, str]]
Reader = Callable[[Iterable[Row], str, str], Iterator[Transaction]]

# 1.234,56 or 12,50 with an optional sign (Rabobank writes +1.250,00): how Dutch banks write amounts.
EURO_AMOUNT = re.compile(r"^[-+]?(\d{1,3}(\.\d{3})+|\d+),\d{2}$")


def parse_amount(text: str, *, decimal_comma: bool) -> Decimal | None:
    """The amount in a statement cell, or ``None`` when the cell holds no amount.

    ``decimal_comma``: ``1.234,56`` (Dutch banks); otherwise ``1234.56``.
    """

    cleaned = text.strip().replace(" ", "").replace(" ", "")
    if not cleaned:
        return None
    if decimal_comma:
        if not EURO_AMOUNT.match(cleaned):
            return None
        return Decimal(cleaned.replace(".", "").replace(",", "."))
    try:
        value = Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _field(row: dict[str, str], name: str, source: str, line: int) -> str:
    if name not in row or row[name] is None:
        raise ImportError_(f"missing column {name!r}", source=source, line=line)
    return row[name]


def _date(text: str, source: str, line: int, formats: tuple[str, ...]):  # noqa: ANN202
    try:
        return parse_bank_date(text, formats=formats)
    except ValueError as exc:
        raise ImportError_(str(exc), source=source, line=line) from None


def read_generic(rows: Iterable[Row], account: str, source: str) -> Iterator[Transaction]:
    for line, row in rows:
        amount = parse_amount(_field(row, "amount", source, line), decimal_comma=False)
        if amount is None:
            raise ImportError_(f"cannot read the amount {row['amount']!r}", source=source, line=line)
        yield Transaction(
            account=account,
            booked=_date(_field(row, "date", source, line), source, line, ("%Y-%m-%d",)),
            amount=amount,
            currency=(row.get("currency") or "EUR").strip().upper(),
            description=squash(row.get("description", "")),
            counterparty=squash(row.get("counterparty", "")),
            counter_iban=(row.get("iban") or "").strip() or None,
            source=f"{source}:{line}",
        )


def read_ing(rows: Iterable[Row], account: str, source: str) -> Iterator[Transaction]:
    for line, row in rows:
        amount = parse_amount(_field(row, "Bedrag (EUR)", source, line), decimal_comma=True)
        if amount is None:
            raise ImportError_(
                f"cannot read the amount {row['Bedrag (EUR)']!r}", source=source, line=line
            )
        direction = _field(row, "Af Bij", source, line).strip()
        if direction not in ("Af", "Bij"):
            raise ImportError_(f"Af Bij must be Af or Bij, not {direction!r}", source=source, line=line)
        name = squash(_field(row, "Naam / Omschrijving", source, line))
        notes = strip_reference(row.get("Mededelingen", ""))
        yield Transaction(
            account=account,
            booked=_date(_field(row, "Datum", source, line), source, line, ("%Y%m%d",)),
            amount=-amount if direction == "Af" else amount,
            currency="EUR",
            description=f"{name} {notes}".strip(),
            counterparty=name,
            counter_iban=(row.get("Tegenrekening") or "").strip() or None,
            source=f"{source}:{line}",
        )


def _is_balance_line(row: dict[str, str]) -> bool:
    return (row.get("Omschrijving-1") or "").strip().lower().startswith("saldo")


def read_rabo(rows: Iterable[Row], account: str, source: str) -> Iterator[Transaction]:
    for line, row in rows:
        cell = _field(row, "Bedrag", source, line)
        amount = parse_amount(cell, decimal_comma=True)
        if amount is None and _is_balance_line(row) and not cell.strip():
            continue
        if amount is None:  # never import a line without its amount: the reports would break
            raise ImportError_(f"cannot read the amount {cell!r}", source=source, line=line)
        yield Transaction(
            account=account,
            booked=_date(_field(row, "Datum", source, line), source, line, ("%Y-%m-%d",)),
            amount=amount,
            currency=(row.get("Munt") or "EUR").strip().upper(),
            description=strip_reference(row.get("Omschrijving-1", "")),
            counterparty=squash(row.get("Naam tegenpartij", "")),
            counter_iban=(row.get("Tegenrekening IBAN/BBAN") or "").strip() or None,
            source=f"{source}:{line}",
        )


READERS: dict[str, Reader] = {"generic": read_generic, "ing": read_ing, "rabo": read_rabo}
DELIMITERS = {"generic": ",", "ing": ";", "rabo": ","}
