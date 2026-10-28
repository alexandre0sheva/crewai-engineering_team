"""A transaction: one line of a bank statement, after import."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    """Money moving into (positive ``amount``) or out of (negative) an asset or liability
    ``account``, in that account's statement currency. ``category`` is the income or expense
    account it belongs to (``None`` until categorised)."""

    account: str
    booked: date
    amount: Decimal
    currency: str
    description: str
    counterparty: str = ""
    counter_iban: str | None = None
    category: str | None = None
    source: str = ""  # "<file>:<line>" it was imported from
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def fingerprint(self) -> str:
        """Stable identity for de-duplication: the same statement line imported twice."""

        key = "|".join(
            [
                self.account,
                self.booked.isoformat(),
                str(self.amount),
                self.currency,
                self.description.strip().lower(),
                (self.counter_iban or "").upper(),
            ]
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @property
    def is_income(self) -> bool:
        return self.amount > 0

    def categorised(self, category: str) -> Transaction:
        return replace(self, category=category)

    def tagged(self, *tags: str) -> Transaction:
        return replace(self, tags=self.tags | frozenset(tags))

    def to_dict(self) -> dict[str, object]:
        return {
            "account": self.account,
            "booked": self.booked.isoformat(),
            "amount": None if self.amount is None else str(self.amount),
            "currency": self.currency,
            "description": self.description,
            "counterparty": self.counterparty,
            "counter_iban": self.counter_iban,
            "category": self.category,
            "source": self.source,
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Transaction:
        raw = data.get("amount")
        return cls(
            account=str(data["account"]),
            booked=date.fromisoformat(str(data["booked"])),
            amount=None if raw is None else Decimal(str(raw)),  # type: ignore[arg-type]
            currency=str(data["currency"]),
            description=str(data.get("description", "")),
            counterparty=str(data.get("counterparty", "")),
            counter_iban=data.get("counter_iban") or None,  # type: ignore[arg-type]
            category=data.get("category") or None,  # type: ignore[arg-type]
            source=str(data.get("source", "")),
            tags=frozenset(data.get("tags") or []),  # type: ignore[arg-type]
        )
