"""``models --refresh-prices``: compare ``pricing.toml`` with what OpenRouter's API reports now.

Live and opt-in. Nothing is rewritten: the differences are printed for a person to review and
copy into ``data/pricing.toml`` (or a ``[pricing]`` override). OpenRouter's models API lists the
first-party models (OpenAI, Anthropic, Google) at their makers' prices, so it is a second source
for those rows too; each row's own ``source_url`` stays the page to confirm a change against.
For an ``openrouter/...`` row the comparison is the same median the row was made from: the hosts
the configured routing allows (tool calling, the allowed quantizations).
"""

from __future__ import annotations

import json
import re
import statistics
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from engineering_team.pricing import TOKENS_PER_PRICE_UNIT, Price

OPENROUTER_API = "https://openrouter.ai/api/v1"
TIMEOUT_SECONDS = 20
TOLERANCE = 0.01  # relative; smaller differences are rounding
FIELDS = (("input", "prompt"), ("cached_input", "input_cache_read"), ("output", "completion"))

Fetch = Callable[[str], Any]  # URL -> parsed JSON


@dataclass(frozen=True)
class PriceDiff:
    model: str
    field: str  # input, cached_input, output, or "listing" when the model is not found
    table: float | None
    found: float | None
    source: str  # the URL the found value came from

    @property
    def text(self) -> str:
        if self.field == "listing":
            return f"{self.model}: not listed at {self.source}; check its own pricing page"
        now = _money(self.found)
        return f"{self.model} {self.field}: pricing.toml {_money(self.table)}, now {now}"


def fetch_json(url: str) -> Any:
    """GET ``url`` (a public API, no key) and parse its JSON."""

    request = urllib.request.Request(url, headers={"User-Agent": "engineering-team"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def openrouter_id(model: str) -> str:
    """The OpenRouter ID of a table model: ``openrouter/x/y`` -> ``x/y``; Anthropic's dashed
    versions become dotted (``claude-sonnet-5-5`` -> ``claude-sonnet-5.5``); ``gemini/`` is
    ``google/``."""

    provider, _, name = model.lower().partition("/")
    if provider == "openrouter":
        return name
    if provider == "anthropic":
        name = re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", name)
    return f"{'google' if provider == 'gemini' else provider}/{name}"


def _per_million(value: Any) -> float | None:
    try:
        return float(value) * TOKENS_PER_PRICE_UNIT
    except (TypeError, ValueError):
        return None


def _prices(pricing: dict[str, Any]) -> dict[str, float]:
    """OpenRouter's per-token prices as ours (USD per million), for the fields it lists."""

    out: dict[str, float] = {}
    for field, key in FIELDS:
        value = _per_million(pricing.get(key))
        if value is not None:
            out[field] = value
    return out


def _differs(table: float | None, found: float | None) -> bool:
    if table is None or found is None:
        return False
    return abs(table - found) > TOLERANCE * max(abs(table), abs(found), 1e-9)


def median_endpoint_prices(
    endpoints: Iterable[dict[str, Any]], quantizations: Sequence[str]
) -> dict[str, float] | None:
    """The median price of the hosts that call tools and serve an allowed quantization."""

    allowed = set(quantizations)
    chosen = [
        e
        for e in endpoints
        if "tools" in (e.get("supported_parameters") or [])
        and (not allowed or (e.get("quantization") or "unknown") in allowed)
    ]
    if not chosen:
        return None
    out: dict[str, float] = {}
    for field, key in FIELDS:
        values = [
            v for e in chosen if (v := _per_million((e.get("pricing") or {}).get(key))) is not None
        ]
        if values:
            out[field] = statistics.median(values)
    return out


def compare(
    prices: Iterable[Price],
    fetch: Fetch,
    *,
    quantizations: Sequence[str],
    on: date | None = None,
) -> list[PriceDiff]:
    """Differences between the shipped rows valid ``on`` (default today) and OpenRouter now."""

    day = on or date.today()
    rows = [p for p in prices if p.covers(day) and not p.is_free and not p.model.endswith("/*")]
    listing_url = f"{OPENROUTER_API}/models"
    catalogue = {m.get("id"): m for m in (fetch(listing_url).get("data") or [])}
    diffs: list[PriceDiff] = []
    for price in sorted(rows, key=lambda p: p.model):
        wanted = openrouter_id(price.model)
        if price.model.startswith("openrouter/"):
            url = f"{OPENROUTER_API}/models/{wanted}/endpoints"
            try:
                endpoints = (fetch(url).get("data") or {}).get("endpoints") or []
            except OSError:  # 404 for a model OpenRouter no longer lists
                endpoints = []
            found = median_endpoint_prices(endpoints, quantizations)
        else:
            url = listing_url
            entry = catalogue.get(wanted)
            pricing = (entry or {}).get("pricing") or {}
            found = None if entry is None else _prices(pricing)
        if found is None:
            diffs.append(PriceDiff(price.model, "listing", None, None, url))
            continue
        for field, _key in FIELDS:
            table = getattr(price, field)
            if _differs(table, found.get(field)):
                diffs.append(PriceDiff(price.model, field, table, found.get(field), url))
    return diffs


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.4g}"
