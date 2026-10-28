"""Errors the command line reports as ``error: <message>`` with exit status 2."""

from __future__ import annotations


class LedgerError(Exception):
    """Something the user can fix: a bad file, an unknown account, a missing rate."""


class ImportError_(LedgerError):  # noqa: N801 - not the builtin
    """A statement that cannot be read. ``line`` is the 1-based line in the file, if known."""

    def __init__(self, message: str, *, source: str = "", line: int | None = None) -> None:
        where = f"{source}:{line}: " if source and line else (f"{source}: " if source else "")
        super().__init__(f"{where}{message}")
        self.source = source
        self.line = line


class RateError(LedgerError):
    """No exchange rate for a currency on a date."""


class RuleError(LedgerError):
    """A categorisation rule file that cannot be used."""
