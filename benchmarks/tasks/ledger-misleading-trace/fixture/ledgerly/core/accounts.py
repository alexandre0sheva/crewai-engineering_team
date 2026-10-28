"""Accounts: where money is (a bank account, a card) and the categories it flows to and from."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ledgerly.core.errors import LedgerError

ACCOUNT_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


class Kind(Enum):
    ASSET = "asset"  # a bank account, cash, a savings account
    LIABILITY = "liability"  # a credit card, a loan
    INCOME = "income"  # a category money comes from
    EXPENSE = "expense"  # a category money goes to

    @property
    def is_category(self) -> bool:
        return self in (Kind.INCOME, Kind.EXPENSE)


@dataclass
class Account:
    id: str
    name: str
    kind: Kind
    currency: str = "EUR"
    iban: str | None = None
    tags: set[str] = field(default_factory=set)
    closed: bool = False

    def __post_init__(self) -> None:
        if not ACCOUNT_ID.match(self.id):
            raise LedgerError(
                f"account id {self.id!r} must be 2-32 lower-case letters, digits or dashes"
            )
        if self.iban is not None:
            self.iban = normalise_iban(self.iban)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "currency": self.currency,
            "iban": self.iban,
            "tags": sorted(self.tags),
            "closed": self.closed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Account:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=Kind(str(data["kind"])),
            currency=str(data.get("currency", "EUR")),
            iban=data.get("iban") or None,  # type: ignore[arg-type]
            tags=set(data.get("tags") or []),  # type: ignore[arg-type]
            closed=bool(data.get("closed", False)),
        )


def normalise_iban(iban: str) -> str:
    compact = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", compact):
        raise LedgerError(f"{iban!r} is not an IBAN")
    if iban_checksum(compact) != 1:
        raise LedgerError(f"{iban!r} has a wrong check digit")
    return compact


def iban_checksum(iban: str) -> int:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97


DEFAULT_CATEGORIES = (
    Account("salary", "Salary", Kind.INCOME),
    Account("interest", "Interest", Kind.INCOME),
    Account("other-income", "Other income", Kind.INCOME),
    Account("groceries", "Groceries", Kind.EXPENSE),
    Account("rent", "Rent", Kind.EXPENSE),
    Account("utilities", "Utilities", Kind.EXPENSE),
    Account("transport", "Transport", Kind.EXPENSE),
    Account("eating-out", "Eating out", Kind.EXPENSE),
    Account("subscriptions", "Subscriptions", Kind.EXPENSE),
    Account("uncategorised", "Uncategorised", Kind.EXPENSE),
)


class Chart:
    """The accounts of a ledger, by id."""

    def __init__(self, accounts: list[Account] | None = None) -> None:
        self._accounts: dict[str, Account] = {}
        for account in accounts if accounts is not None else list(DEFAULT_CATEGORIES):
            self.add(account)

    def add(self, account: Account) -> None:
        if account.id in self._accounts:
            raise LedgerError(f"there is already an account {account.id!r}")
        self._accounts[account.id] = account

    def get(self, account_id: str) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise LedgerError(f"no account {account_id!r}") from None

    def __contains__(self, account_id: object) -> bool:
        return account_id in self._accounts

    def by_kind(self, kind: Kind) -> list[Account]:
        return sorted((a for a in self._accounts.values() if a.kind is kind), key=lambda a: a.id)

    def by_iban(self, iban: str) -> Account | None:
        wanted = normalise_iban(iban)
        return next((a for a in self._accounts.values() if a.iban == wanted), None)

    def all(self) -> list[Account]:
        return sorted(self._accounts.values(), key=lambda a: (a.kind.value, a.id))
