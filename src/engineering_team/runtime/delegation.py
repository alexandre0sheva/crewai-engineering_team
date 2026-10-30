"""Where the lead's delegation tools find the controller, and what each delegated job used.

The ``team`` group's tools are built from the catalogue like any other (``tools/team_tools.py``),
so they cannot be handed the controller object that runs teammates. They reach it through the
run's :class:`DelegationHub` instead: the lead stage opens the hub with its delegator for the
length of the lead session (``pipeline/lead.py``), and outside that session, or for any agent but
the lead, the tools refuse. The hub is also an event sink: model calls and commands made in a
delegation's lane (``D1``, ``D2``, ...) are counted per lane, so each delegation's result says
what it cost and which checks its teammate ran.
"""

from __future__ import annotations

import contextlib
import threading
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from engineering_team.pricing import PriceTable, estimate_cost
from engineering_team.runtime.events import current_lane

LANE_PREFIX = "D"
# Tools whose calls a delegation's result lists as "checks it ran".
CHECK_TOOLS = frozenset(
    {"Run Tests", "Run Project Command", "Run Script", "Run Linter", "Type Check", "Build Project"}
)
MAX_COMMANDS = 8


class Delegating(Protocol):
    """The controller side of delegation, as the ``team`` tools see it."""

    lead: str

    def handle(self, tool: str, arguments: dict[str, Any]) -> str: ...


@dataclass
class LaneUsage:
    """What one delegation lane used: model calls by model, and the commands it ran."""

    calls: int = 0
    tokens: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    commands: list[str] = field(default_factory=list)

    def cost(self, prices: PriceTable) -> float | None:
        total = 0.0
        for model, counts in self.tokens.items():
            cost = estimate_cost(
                prices.lookup(model),
                prompt_tokens=counts.get("prompt_tokens", 0),
                completion_tokens=counts.get("completion_tokens", 0),
                cached_prompt_tokens=counts.get("cached_prompt_tokens", 0),
                cache_creation_tokens=counts.get("cache_creation_tokens", 0),
            )
            if cost is None:
                return None
            total += cost
        return total

    @property
    def total_tokens(self) -> int:
        return sum(
            c.get("prompt_tokens", 0) + c.get("completion_tokens", 0) for c in self.tokens.values()
        )


def is_delegation_lane(lane: object) -> bool:
    return isinstance(lane, str) and lane.startswith(LANE_PREFIX) and lane[1:].isdigit()


class DelegationHub:
    """The run's delegation slot and per-lane meter (one per ``RunContext``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Delegating | None = None
        self._lanes: dict[str, LaneUsage] = defaultdict(LaneUsage)

    @property
    def active(self) -> Delegating | None:
        with self._lock:
            return self._active

    @contextlib.contextmanager
    def opened(self, delegator: Delegating) -> Iterator[Delegating]:
        """Let the lead's tools reach ``delegator`` while the lead session runs."""

        with self._lock:
            self._active = delegator
        try:
            yield delegator
        finally:
            with self._lock:
                self._active = None

    def usage(self, lane: str) -> LaneUsage:
        with self._lock:
            found = self._lanes.get(lane)
            return (
                LaneUsage(
                    found.calls, {m: dict(c) for m, c in found.tokens.items()}, list(found.commands)
                )
                if found
                else LaneUsage()
            )

    # -- event sink -------------------------------------------------------------------------

    def emit(self, type: str, **data: Any) -> None:
        if type not in ("llm.call", "tool.call"):
            return
        lane = data.get("lane")
        lane = current_lane.get() if lane is None else lane
        if not is_delegation_lane(lane):
            return
        with self._lock:
            used = self._lanes[str(lane)]
            if type == "llm.call":
                used.calls += 1
                counts = used.tokens[str(data.get("model") or "unknown")]
                for name, value in (data.get("usage") or {}).items():
                    if isinstance(value, int):
                        counts[name] = counts.get(name, 0) + value
            elif data.get("tool") in CHECK_TOOLS and len(used.commands) < MAX_COMMANDS:
                args = data.get("args") or {}
                what = " ".join(
                    str(args.get(k)) for k in ("command", "name", "args") if args.get(k)
                )
                status = "ok" if data.get("ok", True) else "failed"
                used.commands.append(f"{data.get('tool')} {what}".strip() + f" ({status})")
