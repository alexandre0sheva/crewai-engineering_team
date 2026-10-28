"""Rules that put transactions in categories. The first rule that matches wins; income and
expenses with no matching rule fall back to ``other-income`` and ``uncategorised``."""

from __future__ import annotations

from dataclasses import dataclass, field

from ledgerly.core.errors import RuleError
from ledgerly.core.transactions import Transaction
from ledgerly.rules.matchers import Matcher, parse_matcher

BUILT_IN = (
    ("salary", ["description regex \\b(salaris|salary|loon)\\b"]),
    ("interest", ["description contains rente"]),
    ("groceries", ["counterparty regex (albert heijn|jumbo|lidl|aldi|plus)"]),
    ("rent", ["description contains huur"]),
    ("utilities", ["counterparty regex (vattenfall|eneco|essent|waternet|ziggo|kpn)"]),
    ("transport", ["counterparty regex (\\bns\\b|ov-chipkaart|gvb|ret|shell|bp)"]),
    ("eating-out", ["description regex (restaurant|cafe|café|thuisbezorgd)"]),
    ("subscriptions", ["counterparty regex (spotify|netflix|disney|icloud)"]),
)


@dataclass
class Rule:
    category: str
    matchers: list[Matcher]
    name: str = ""
    income_only: bool = False
    expense_only: bool = False
    hits: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        if not self.matchers:
            raise RuleError(f"rule {self.name or self.category!r} has no matchers")
        if self.income_only and self.expense_only:
            raise RuleError("a rule cannot be for income only and expenses only")

    def matches(self, transaction: Transaction) -> bool:
        if self.income_only and not (transaction.amount or 0) > 0:
            return False
        if self.expense_only and not (transaction.amount or 0) < 0:
            return False
        return all(m.matches(transaction) for m in self.matchers)

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "name": self.name,
            "matchers": [m.to_dict() for m in self.matchers],
            "income_only": self.income_only,
            "expense_only": self.expense_only,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Rule:
        raw = data.get("matchers") or []
        return cls(
            category=str(data["category"]),
            matchers=[Matcher(**item) for item in raw],  # type: ignore[union-attr, arg-type]
            name=str(data.get("name", "")),
            income_only=bool(data.get("income_only", False)),
            expense_only=bool(data.get("expense_only", False)),
        )


def built_in_rules() -> list[Rule]:
    return [
        Rule(category, [parse_matcher(text) for text in texts], name=f"built-in {category}")
        for category, texts in BUILT_IN
    ]


class Categorizer:
    """The ledger's own rules first, then the built-in ones."""

    def __init__(self, rules: list[Rule], *, use_built_in: bool = True) -> None:
        self.rules = list(rules) + (built_in_rules() if use_built_in else [])

    def category_for(self, transaction: Transaction) -> str:
        for rule in self.rules:
            if rule.matches(transaction):
                rule.hits += 1
                return rule.category
        return "other-income" if (transaction.amount or 0) > 0 else "uncategorised"

    def explain(self, transaction: Transaction) -> str:
        for rule in self.rules:
            if rule.matches(transaction):
                return f"{rule.category} (rule {rule.name or rule.category})"
        return f"{self.category_for(transaction)} (no rule matched)"
