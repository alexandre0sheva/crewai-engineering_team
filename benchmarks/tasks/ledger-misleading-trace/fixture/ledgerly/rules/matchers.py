"""What a rule can match on: words in the description, the counterparty, the IBAN, the amount."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ledgerly.core.errors import RuleError
from ledgerly.core.transactions import Transaction
from ledgerly.util.text import fold


@dataclass(frozen=True)
class Matcher:
    field: str  # description, counterparty, iban, amount
    op: str  # contains, equals, regex, below, above, between
    value: str

    def __post_init__(self) -> None:
        allowed = OPERATORS.get(self.field)
        if allowed is None:
            raise RuleError(f"rules cannot match on {self.field!r}")
        if self.op not in allowed:
            raise RuleError(f"{self.field} matchers use {', '.join(allowed)}, not {self.op!r}")
        if self.op == "regex":
            try:
                re.compile(self.value)
            except re.error as exc:
                raise RuleError(f"bad regular expression {self.value!r}: {exc}") from None
        if self.field == "amount":
            for part in self.value.split(".."):
                try:
                    Decimal(part)
                except ArithmeticError:
                    raise RuleError(f"{self.value!r} is not an amount") from None

    def matches(self, transaction: Transaction) -> bool:
        if self.field == "amount":
            return _amount_matches(self.op, self.value, transaction.amount)
        if self.field == "iban":
            have = (transaction.counter_iban or "").replace(" ", "").upper()
            return have == self.value.replace(" ", "").upper()
        text = transaction.description if self.field == "description" else transaction.counterparty
        if self.op == "contains":
            return fold(self.value) in fold(text)
        if self.op == "equals":
            return fold(self.value) == fold(text)
        return re.search(self.value, text, re.IGNORECASE) is not None

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "op": self.op, "value": self.value}


OPERATORS = {
    "description": ("contains", "equals", "regex"),
    "counterparty": ("contains", "equals", "regex"),
    "iban": ("equals",),
    "amount": ("below", "above", "between"),
}


def _amount_matches(op: str, value: str, amount: Decimal | None) -> bool:
    if amount is None:
        return False
    size = abs(amount)
    if op == "below":
        return size < Decimal(value)
    if op == "above":
        return size > Decimal(value)
    low, high = (Decimal(part) for part in value.split("..", 1))
    return low <= size <= high


def parse_matcher(text: str) -> Matcher:
    """``"description contains albert heijn"`` -> a matcher."""

    parts = text.strip().split(None, 2)
    if len(parts) != 3:
        raise RuleError(f"a matcher is FIELD OP VALUE, not {text!r}")
    return Matcher(parts[0].lower(), parts[1].lower(), parts[2])
