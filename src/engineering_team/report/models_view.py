"""The report's model routing and fallbacks: the route a task took and every time another model
stood in for the one a tier uses (``model.route`` and ``model.fallback`` events)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from engineering_team.contracts import Event, UsageReport
from engineering_team.report.fmt import money

REASONS = {
    "rate_limit": "rate limit",
    "timeout": "timeout",
    "provider_error": "provider error",
    "cooldown": "primary cooling down after a failure",
    "error": "error",
}


@dataclass(frozen=True)
class FallbackUse:
    """Calls a fallback model answered in place of ``from_model``, and why. ``cost_usd`` is what
    the whole run spent on ``to_model`` (its calls as a fallback and any other)."""

    from_model: str
    to_model: str
    reason: str
    calls: int
    cost_usd: float | None = None


@dataclass(frozen=True)
class ModelNotes:
    route: str = ""
    fallbacks: tuple[FallbackUse, ...] = ()


def collect(events: Iterable[Event], usage: UsageReport | None) -> ModelNotes:
    route = ""
    counts: Counter[tuple[str, str, str]] = Counter()
    for event in events:
        if event.type == "model.route":
            route = str(event.data.get("summary") or event.data.get("route") or "")
        elif event.type == "model.fallback":
            data = event.data
            key = (str(data.get("from_model")), str(data.get("to_model")), str(data.get("reason")))
            counts[key] += 1
    costs = usage.cost_by_model if usage is not None else {}
    uses = [
        FallbackUse(source, target, reason, calls, costs.get(target))
        for (source, target, reason), calls in sorted(counts.items())
    ]
    return ModelNotes(route=route, fallbacks=tuple(uses))


FALLBACK_HEAD = ["Failed model", "Answered by", "Why", "Calls", "Cost of that model"]


def fallback_rows(notes: ModelNotes) -> list[tuple[str, str, str, int, str]]:
    return [
        (u.from_model, u.to_model, reason_text(u.reason), u.calls, cost_text(u))
        for u in notes.fallbacks
    ]


def reason_text(reason: str) -> str:
    return REASONS.get(reason, reason)


def cost_text(use: FallbackUse) -> str:
    return money(use.cost_usd)


def cache_rows(usage: UsageReport) -> list[tuple[str, str]]:
    """Prompt-cache facts for the usage table: what share of the prompt tokens was read from the
    provider's cache, and the tokens written to it (Anthropic reports those)."""

    totals = usage.totals
    rows: list[tuple[str, str]] = []
    if totals.prompt_tokens:
        share = totals.cached_prompt_tokens / totals.prompt_tokens
        rows.append(("Cache hits", f"{share:.0%} of prompt tokens"))
    if totals.cache_creation_tokens:
        rows.append(("Cache writes", f"{totals.cache_creation_tokens:,} tokens"))
    return rows
