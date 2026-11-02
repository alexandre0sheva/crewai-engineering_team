"""Token and tool-call accounting, collected from the run's event stream.

``UsageTracker`` is an event sink: it counts ``llm.call`` events (bridged from CrewAI, with
token usage) per ``(stage, agent, model)`` and ``tool.call`` events, and can turn that into a
:class:`UsageReport` with estimated costs. The report can always be rebuilt from
``events.jsonl`` (:meth:`UsageTracker.from_events`), so a crash loses nothing.

A provider that reports what a call was billed (OpenRouter's ``usage.cost``) is costed by that:
a row whose every call reported a cost uses their sum, any other row the price table.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from engineering_team.contracts import Event, UsageReport, UsageRow, UsageTotals
from engineering_team.pricing import PriceTable, estimate_cost
from engineering_team.runtime.events import current_stage

_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_prompt_tokens",
    "reasoning_tokens",
    "cache_creation_tokens",
)
_Key = tuple[str | None, str | None, str]


class UsageTracker:
    """Thread-safe usage counters. Register it as an event sink; it ignores other events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[_Key, dict[str, int]] = defaultdict(
            lambda: dict.fromkeys((*_FIELDS, "calls"), 0)
        )
        self._reported: dict[_Key, list[float]] = defaultdict(list)
        self._tool_calls = 0

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> UsageTracker:
        tracker = cls()
        for event in events:
            tracker.emit(event.type, stage=event.stage, agent=event.agent, **event.data)
        return tracker

    def emit(self, type: str, **data: Any) -> None:
        if type == "tool.call":
            with self._lock:
                self._tool_calls += 1
            return
        if type != "llm.call":
            return
        usage = data.get("usage") or {}
        key: _Key = (
            data.get("stage") or current_stage.get(),
            data.get("agent"),
            str(data.get("model") or "unknown"),
        )
        with self._lock:
            row = self._rows[key]
            row["calls"] += 1
            for name in _FIELDS:
                row[name] += int(usage.get(name) or 0)
            reported = usage.get("cost_usd")
            if isinstance(reported, (int, float)) and not isinstance(reported, bool):
                self._reported[key].append(float(reported))

    @property
    def tool_calls(self) -> int:
        with self._lock:
            return self._tool_calls

    def totals(self) -> UsageTotals:
        with self._lock:
            return _totals(self._rows.values())

    def report(self, prices: PriceTable) -> UsageReport:
        with self._lock:
            snapshot = {key: dict(row) for key, row in self._rows.items()}
            reported = {key: list(costs) for key, costs in self._reported.items()}
            tool_calls = self._tool_calls
        rows: list[UsageRow] = []
        for key, counters in sorted(
            snapshot.items(), key=lambda item: tuple(str(part or "") for part in item[0])
        ):
            stage, agent, model = key
            billed = reported.get(key, [])
            cost: float | None
            if billed and len(billed) == counters["calls"]:
                cost, source = sum(billed), "provider"
            else:
                cost = estimate_cost(
                    prices.lookup(model),
                    prompt_tokens=counters["prompt_tokens"],
                    completion_tokens=counters["completion_tokens"],
                    cached_prompt_tokens=counters["cached_prompt_tokens"],
                    cache_creation_tokens=counters["cache_creation_tokens"],
                )
                source = "price table"
            rows.append(
                UsageRow(
                    stage=stage,
                    agent=agent,
                    model=model,
                    cost_usd=cost,
                    cost_source=source if cost is not None else None,
                    **_totals([counters]).model_dump(exclude={"schema_version"}),
                )
            )
        costs: dict[str, float | None] = {}
        for model in sorted({row.model for row in rows}):
            known = [row.cost_usd for row in rows if row.model == model]
            costs[model] = (
                None if any(c is None for c in known) else sum(c for c in known if c is not None)
            )
        unpriced = [model for model, cost in costs.items() if cost is None]
        known_total = sum(cost for cost in costs.values() if cost is not None)
        return UsageReport(
            totals=_totals(snapshot.values()),
            tool_calls=tool_calls,
            estimated_cost_usd=None if unpriced else known_total,
            known_cost_usd=known_total,
            unpriced_models=unpriced,
            by_model=_group(rows, lambda row: row.model),
            by_agent=_group(rows, lambda row: row.agent or "unknown"),
            by_stage=_group(rows, lambda row: row.stage or "unstaged"),
            cost_by_model=costs,
            rows=rows,
        )


def _totals(rows: Iterable[dict[str, int]]) -> UsageTotals:
    summed = dict.fromkeys((*_FIELDS, "calls"), 0)
    for row in rows:
        for name in summed:
            summed[name] += row[name]
    return UsageTotals(total_tokens=summed["prompt_tokens"] + summed["completion_tokens"], **summed)


def _group(rows: list[UsageRow], key: Any) -> dict[str, UsageTotals]:
    buckets: dict[str, list[dict[str, int]]] = defaultdict(list)
    for row in rows:
        buckets[key(row)].append({name: getattr(row, name) for name in (*_FIELDS, "calls")})
    return {name: _totals(items) for name, items in sorted(buckets.items())}
