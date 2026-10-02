"""Model prices and cost estimates.

Prices live in ``data/pricing.toml`` (USD per million tokens, each row with its source and the
date it was checked) and can be overridden per model in the user's config. A model with no
price has an *unknown* cost: functions return ``None`` and callers show "unknown", never $0.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from importlib import resources
from typing import Any

TOKENS_PER_PRICE_UNIT = 1_000_000

# CrewAI names Google's provider prefix "gemini"; users often write "google".
_PROVIDER_ALIASES = {"google": "gemini"}


@dataclass(frozen=True)
class Price:
    """USD per million tokens for one model."""

    model: str
    input: float
    output: float
    cached_input: float | None = None
    cache_write: float | None = None
    source_url: str = ""
    as_of: date | None = None
    note: str = ""
    valid_from: date | None = None
    valid_until: date | None = None
    overridden: bool = False

    def covers(self, day: date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_until is None or day <= self.valid_until
        )

    @property
    def is_free(self) -> bool:
        return self.input == 0 and self.output == 0


def normalize_model(model: str) -> str:
    """Lower-case ``provider/model`` with provider aliases resolved."""

    text = model.strip().lower()
    provider, slash, name = text.partition("/")
    return f"{_PROVIDER_ALIASES.get(provider, provider)}/{name}" if slash else text


def _matches(pattern: str, wanted: str) -> bool:
    """Does a table key cover ``wanted``? Keys may end in ``/*`` to cover a whole provider;
    a bare ``wanted`` (no provider) matches the model part of a key."""

    pattern, wanted = normalize_model(pattern), normalize_model(wanted)
    if pattern.endswith("/*"):
        return "/" in wanted and wanted.startswith(pattern[:-1])
    if pattern == wanted:
        return True
    return "/" not in wanted and pattern.partition("/")[2] == wanted


class PriceTable:
    """Default prices plus the user's overrides; overrides always win."""

    def __init__(self, prices: Iterable[Price], overrides: Mapping[str, Price] | None = None):
        self._prices = list(prices)
        self._overrides = dict(overrides or {})

    @property
    def prices(self) -> list[Price]:
        return list(self._prices)

    def lookup(self, model: str, *, on: date | None = None) -> Price | None:
        """The price of ``model`` on ``on`` (default today), or ``None`` if unknown.

        A bare name such as ``gpt-6-luna`` matches ``openai/gpt-6-luna`` only when exactly one
        provider has a model of that name, so an ambiguous name is unknown rather than guessed.
        """

        day = on or date.today()
        for source in (self._overrides.values(), (p for p in self._prices if p.covers(day))):
            found = {p.model: p for p in source if _matches(p.model, model)}
            if len(found) == 1:
                return next(iter(found.values()))
            if len(found) > 1:
                exact = [
                    p for key, p in found.items() if normalize_model(key) == normalize_model(model)
                ]
                return exact[0] if len(exact) == 1 else None
        return None


def estimate_cost(
    price: Price | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """USD cost of one usage record, or ``None`` when the model's price is unknown.

    ``prompt_tokens`` is the full billed prompt (CrewAI includes cache reads and writes in it),
    so cached and cache-write tokens are split out of it and priced at their own rates.
    """

    if price is None:
        return None
    written = min(max(cache_creation_tokens, 0), prompt_tokens)
    cached = min(max(cached_prompt_tokens, 0), prompt_tokens - written)
    fresh = prompt_tokens - written - cached
    cached_rate = price.input if price.cached_input is None else price.cached_input
    write_rate = price.input if price.cache_write is None else price.cache_write
    total = (
        fresh * price.input
        + cached * cached_rate
        + written * write_rate
        + completion_tokens * price.output
    )
    return total / TOKENS_PER_PRICE_UNIT


def _number(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    return None if value is None else float(value)


def _day(row: Mapping[str, Any], key: str) -> date | None:
    value = row.get(key)
    return value if isinstance(value, date) else None


def _price_from_mapping(row: Mapping[str, Any], *, overridden: bool = False) -> Price:
    return Price(
        model=str(row["model"]),
        input=float(row["input"]),
        output=float(row["output"]),
        cached_input=_number(row, "cached_input"),
        cache_write=_number(row, "cache_write"),
        source_url=str(row.get("source_url", "")),
        as_of=_day(row, "as_of"),
        note=str(row.get("note", "")),
        valid_from=_day(row, "valid_from"),
        valid_until=_day(row, "valid_until"),
        overridden=overridden,
    )


@cache
def default_prices() -> tuple[Price, ...]:
    text = (resources.files("engineering_team") / "data" / "pricing.toml").read_text("utf-8")
    return tuple(_price_from_mapping(row) for row in tomllib.loads(text)["price"])


def build_table(overrides: Mapping[str, Mapping[str, Any]] | None = None) -> PriceTable:
    """The shipped prices with ``overrides`` (``{"provider/model": {"input": ..}}``) applied."""

    prices = {
        model: replace(
            _price_from_mapping({"model": model, **fields}, overridden=True),
            note="user override",
        )
        for model, fields in (overrides or {}).items()
    }
    return PriceTable(default_prices(), prices)


def format_price(price: Price) -> str:
    """``$2/$10 per MTok`` (or ``free``) for ``config show``."""

    if price.is_free:
        return "free"
    return f"${price.input:g}/${price.output:g} per MTok"
