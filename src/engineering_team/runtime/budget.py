"""Run budgets: cost, tokens, wall time, and tool calls, enforced cooperatively.

Stopping works in two steps because the pieces that overspend cannot be interrupted safely:

1. *Tripping.* The guard watches ``llm.call`` events (token usage arrives from CrewAI's event
   bus after each model call), and refuses tool calls through the run's ``tool_gate``. When a
   limit is passed it records a :class:`BudgetExceeded`, emits ``budget.exceeded``, and sets
   the run's ``cancel_event``. Every tool then answers "run cancelled", so agents wind down.
2. *Raising.* At the next safe point (a stage boundary, or the end of the run) :meth:`check`
   raises the recorded ``BudgetExceeded``.

Honest limits: a model call that is already in flight when a limit trips still completes and
is paid for, and usage is only seen when the call's event is delivered. With several agents
running at once, overrun can be one in-flight call per agent. Treat budgets as a safety net
that bounds the damage, not as a hard cap to the cent. A cost budget also needs a known price
for every model used; with an unpriced model it cannot be enforced and the status says so.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from engineering_team.contracts import BudgetLimitStatus, BudgetState, BudgetStatus
from engineering_team.pricing import PriceTable
from engineering_team.runtime.events import EventSink, NullSink
from engineering_team.runtime.usage import UsageTracker

WARN_FRACTION = 0.8


@dataclass(frozen=True)
class Budget:
    """Limits for one run. ``None`` means unlimited."""

    max_cost_usd: float | None = None
    max_tokens: int | None = None
    max_wall_seconds: float | None = None
    max_tool_calls: int | None = None
    max_repair_rounds: int = 3

    @classmethod
    def from_settings(cls, settings: Any) -> Budget:
        """From ``Settings.budget`` (anything with the same field names)."""

        return cls(
            max_cost_usd=settings.max_cost_usd,
            max_tokens=settings.max_tokens,
            max_wall_seconds=settings.max_wall_seconds,
            max_tool_calls=settings.max_tool_calls,
            max_repair_rounds=settings.max_repair_rounds,
        )


class BudgetExceeded(RuntimeError):
    """A run passed one of its limits."""

    def __init__(self, limit: str, used: float, maximum: float) -> None:
        self.limit = limit
        self.used = used
        self.maximum = maximum
        super().__init__(
            f"Budget exceeded: {limit} reached {_fmt(limit, used)} (limit {_fmt(limit, maximum)}). "
            "In-flight model calls still finish and are billed; raise the limit in the budget "
            "settings to continue."
        )


def _fmt(limit: str, value: float) -> str:
    return f"${value:.4f}" if limit == "max_cost_usd" else f"{value:g}"


class BudgetGuard:
    """Tracks one run against its :class:`Budget`.

    It is an event sink (register it after the :class:`UsageTracker` so counts are current)
    and provides ``tool_gate`` for ``RunContext``. ``check()`` is the safe-point call.
    """

    def __init__(
        self,
        budget: Budget,
        usage: UsageTracker,
        prices: PriceTable,
        cancel_event: threading.Event,
        *,
        events: EventSink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._usage = usage
        self._prices = prices
        self._cancel = cancel_event
        self._events: EventSink = events or NullSink()
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._exceeded: BudgetExceeded | None = None
        self._warned: set[str] = set()
        self._noted: list[str] = []

    def attach(self, events: EventSink) -> None:
        """Where ``budget.*`` events go (the run's own sink, set once it exists)."""

        self._events = events

    # -- measuring ------------------------------------------------------------------------

    def _used(self) -> dict[str, float]:
        """Current usage for every limit that is configured."""

        used: dict[str, float] = {}
        budget = self.budget
        if budget.max_tokens is not None:
            used["max_tokens"] = float(self._usage.totals().total_tokens)
        if budget.max_wall_seconds is not None:
            used["max_wall_seconds"] = self._clock() - self._started
        if budget.max_tool_calls is not None:
            used["max_tool_calls"] = float(self._usage.tool_calls)
        if budget.max_cost_usd is not None:
            report = self._usage.report(self._prices)
            used["max_cost_usd"] = report.known_cost_usd
            note = (
                "max_cost_usd cannot be fully enforced: no known price for "
                + ", ".join(report.unpriced_models)
                if report.unpriced_models
                else None
            )
            if note and note not in self._noted:
                self._noted.append(note)
                self._events.emit("budget.warning", limit="max_cost_usd", note=note)
        return used

    def _limits(self) -> list[BudgetLimitStatus]:
        used = self._used()
        statuses = []
        for name, value in used.items():
            maximum = float(getattr(self.budget, name))
            statuses.append(
                BudgetLimitStatus(name=name, used=value, max=maximum, fraction=value / maximum)
            )
        return statuses

    def status(self) -> BudgetStatus:
        """The run's standing against every configured limit (never raises)."""

        limits = self._limits()
        with self._lock:
            exceeded, notes = self._exceeded, list(self._noted)
        state: BudgetState = "unlimited" if not limits else "ok"
        if any(limit.fraction >= WARN_FRACTION for limit in limits):
            state = "warning"
        if exceeded is not None or any(limit.fraction > 1 for limit in limits):
            state = "exceeded"
        return BudgetStatus(
            state=state,
            limits=limits,
            exceeded=str(exceeded) if exceeded else None,
            notes=notes,
        )

    # -- enforcing ------------------------------------------------------------------------

    @property
    def tripped(self) -> BudgetExceeded | None:
        """The recorded overspend, if a limit has been passed."""

        with self._lock:
            return self._exceeded

    def _trip(self, limit: str, used: float, maximum: float) -> BudgetExceeded:
        with self._lock:
            first = self._exceeded is None
            if first:
                self._exceeded = BudgetExceeded(limit, used, maximum)
            exceeded = self._exceeded
        assert exceeded is not None
        if first:
            self._events.emit(
                "budget.exceeded", limit=limit, used=used, max=maximum, message=str(exceeded)
            )
            self._cancel.set()  # cooperative: tools refuse, agents wind down
        return exceeded

    def evaluate(self) -> BudgetStatus:
        """Update warnings and trip on any passed limit; never raises."""

        status = self.status()
        for limit in status.limits:
            if limit.fraction > 1:
                self._trip(limit.name, limit.used, limit.max)
            elif limit.fraction >= WARN_FRACTION and limit.name not in self._warned:
                self._warned.add(limit.name)
                self._events.emit(
                    "budget.warning",
                    limit=limit.name,
                    used=limit.used,
                    max=limit.max,
                    fraction=round(limit.fraction, 3),
                )
        return self.status()

    def check(self) -> BudgetStatus:
        """The safe-point call: raise :class:`BudgetExceeded` if any limit has been passed."""

        status = self.evaluate()
        exceeded = self.tripped
        if exceeded is not None:
            raise exceeded
        return status

    def emit(self, type: str, **data: Any) -> None:
        """Event-sink hook: re-evaluate after each model call's usage arrives."""

        if type == "llm.call":
            self.evaluate()

    def tool_gate(self, tool: str) -> str | None:
        """Refuse a tool call that would pass the tool-call or wall-clock limit."""

        if self._exceeded is not None:
            return str(self._exceeded)
        budget = self.budget
        if budget.max_tool_calls is not None and self._usage.tool_calls >= budget.max_tool_calls:
            return str(
                self._trip("max_tool_calls", self._usage.tool_calls + 1, budget.max_tool_calls)
            )
        if budget.max_wall_seconds is not None:
            elapsed = self._clock() - self._started
            if elapsed > budget.max_wall_seconds:
                return str(self._trip("max_wall_seconds", elapsed, budget.max_wall_seconds))
        return None

    def may_repair(self, rounds_used: int) -> bool:
        """May another repair round start after ``rounds_used`` rounds?"""

        return rounds_used < self.budget.max_repair_rounds
