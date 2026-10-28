"""Importing a statement file into the journal: read, categorise, de-duplicate."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ledgerly.core.errors import ImportError_
from ledgerly.core.journal import Journal
from ledgerly.core.transactions import Transaction
from ledgerly.io.bank_formats import DELIMITERS, READERS, Row
from ledgerly.rules.categorize import Categorizer

ENCODINGS = ("utf-8-sig", "cp1252")
MAX_BYTES = 20 * 1024 * 1024


@dataclass
class ImportResult:
    source: str
    read: int = 0
    added: int = 0
    duplicates: int = 0
    uncategorised: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.added} added", f"{self.duplicates} already there"]
        if self.uncategorised:
            parts.append(f"{self.uncategorised} uncategorised")
        return f"{self.source}: {self.read} read, " + ", ".join(parts)


def decode(data: bytes, source: str) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("the file is neither UTF-8 nor Windows-1252 text", source=source)


def rows(text: str, delimiter: str, source: str) -> Iterator[Row]:
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if not reader.fieldnames:
        raise ImportError_("the file is empty", source=source)
    for row in reader:
        if None in row:
            raise ImportError_(
                f"{len(row[None])} more field(s) than the header", source=source, line=reader.line_num
            )
        if not any((value or "").strip() for value in row.values()):
            continue
        yield reader.line_num, {k.strip(): (v or "") for k, v in row.items() if k}


def read_statement(path: Path, fmt: str, account: str) -> list[Transaction]:
    if fmt not in READERS:
        raise ImportError_(f"unknown format {fmt!r} (use {', '.join(READERS)})", source=path.name)
    try:
        data = path.read_bytes()
    except OSError:
        raise ImportError_("cannot read the file", source=path.name) from None
    if len(data) > MAX_BYTES:
        raise ImportError_("the file is larger than 20 MB", source=path.name)
    text = decode(data, path.name)
    return list(READERS[fmt](rows(text, DELIMITERS[fmt], path.name), account, path.name))


def import_statement(
    journal: Journal, path: Path, fmt: str, account: str, categorizer: Categorizer
) -> ImportResult:
    """Add the statement's transactions to ``journal`` (all of them, or none on an error)."""

    result = ImportResult(source=path.name)
    transactions = read_statement(path, fmt, account)
    result.read = len(transactions)
    known = {t.currency for t in transactions} - {journal.chart.get(account).currency}
    if known:
        result.warnings.append(
            f"{path.name} has amounts in {', '.join(sorted(known))}; reports convert them"
        )
    categorised = []
    for transaction in transactions:
        category = categorizer.category_for(transaction)
        if category == "uncategorised":
            result.uncategorised += 1
        categorised.append(transaction.categorised(category))
    result.added, result.duplicates = journal.extend(categorised)
    return result
