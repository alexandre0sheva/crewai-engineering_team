"""Agent sessions: what each agent's turn on a task cost, read back from a run's records.

A session is one agent working on one task (a stage, a work package, a delegation): it starts
with CrewAI's ``agent.started`` and ends with ``agent.completed`` or ``agent.error``. Everything
the run logged in between belongs to it: model calls (``llm.call``, with token usage), the prompts
they were sent (``llm.request``, sized by part) and tool calls (``tool.call``, whose failures are
classified from the trajectory). The log is the only source, so a crashed or still-running run
can be profiled as well; a session that never ended is reported ``open``.

CrewAI delivers events on a thread pool, so the order of events in ``events.jsonl`` is not their
order of emission: bridged events carry CrewAI's own ``emission`` number and are matched to
sessions by it; tool calls (ours, without one) are matched by time, stage and lane.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from engineering_team.contracts import Event, SessionUsage
from engineering_team.pricing import PriceTable, estimate_cost
from engineering_team.runtime.prompt_parts import CHARS_PER_TOKEN, PARTS
from engineering_team.runtime.trajectory import TrajectoryEntry

GRACE = timedelta(seconds=1)  # a handler thread may record an event a moment late
_Key = tuple[str | None, str | None, str | None]

# (pattern in the tool's ERROR message, error kind): the first match wins.
ERROR_KINDS: tuple[tuple[str, str], ...] = (
    (r"cannot move from|allowed next states", "invalid-state"),
    (r"must stay inside|outside the project|outside the workspace", "path-outside-workspace"),
    (r"write scope|outside .*scope|not owned by", "write-scope"),
    (r"was cancelled|run was cancelled", "cancelled"),
    (r"budget|limit reached|tool-call limit", "budget"),
    (r"timed out|timeout", "timeout"),
    (r"not allowed|allowlist|not in the allowed|not permitted|blocked", "command-not-allowed"),
    (r"old_text|did not match|no match|not found in", "edit-no-match"),
    (r"file not found|no such file|does not exist|not a file|not a directory", "path-not-found"),
    (
        r"no (build|test|lint) command|no script named|no project files|nothing to run",
        "nothing-to-run",
    ),
    (r"malformed|invalid|missing|required|must be|expected", "bad-arguments"),
)


def error_kind(message: str) -> str:
    """A short, stable name for why a tool call failed (``other`` when nothing matches)."""

    text = message.removeprefix("ERROR:").strip().lower()
    for pattern, kind in ERROR_KINDS:
        if re.search(pattern, text):
            return kind
    return "other"


@dataclass
class _Session:
    id: str
    stage: str | None
    agent: str | None
    lane: int | str | None
    task: str | None
    started: datetime
    start_emission: int
    ended: datetime | None = None
    end_emission: int | None = None
    ended_with: str = "open"
    calls: list[Event] = field(default_factory=list)
    requests: list[Event] = field(default_factory=list)
    tools: list[Event] = field(default_factory=list)

    def covers(self, moment: datetime) -> bool:
        return self.started - GRACE <= moment and (
            self.ended is None or moment <= self.ended + GRACE
        )


def _emission(event: Event) -> int:
    value = event.data.get("emission")
    return value if isinstance(value, int) else 0


def _key(event: Event) -> _Key:
    return (event.stage, event.agent, None if event.lane is None else str(event.lane))


def _open_sessions(events: Sequence[Event]) -> list[_Session]:
    """Pair every ``agent.started`` with the next end of the same agent, stage and lane."""

    starts = sorted((e for e in events if e.type == "agent.started"), key=_emission)
    ends = sorted(
        (e for e in events if e.type in ("agent.completed", "agent.error")), key=_emission
    )
    sessions: list[_Session] = []
    taken: set[int] = set()
    for number, start in enumerate(starts, start=1):
        session = _Session(
            id=f"S{number}",
            stage=start.stage,
            agent=start.agent,
            lane=start.lane,
            task=(start.data.get("task") or None),
            started=start.ts,
            start_emission=_emission(start),
        )
        later = [s for s in starts if _key(s) == _key(start) and _emission(s) > _emission(start)]
        limit = _emission(later[0]) if later else None
        for end in ends:
            if end.seq in taken or _key(end) != _key(start) or _emission(end) <= _emission(start):
                continue
            if limit is not None and _emission(end) > limit:
                break
            taken.add(end.seq)
            session.ended, session.end_emission = end.ts, _emission(end)
            session.ended_with = "completed" if end.type == "agent.completed" else "error"
            break
        sessions.append(session)
    return sessions


def _assign(events: Sequence[Event], sessions: list[_Session]) -> None:
    by_key: dict[_Key, list[_Session]] = defaultdict(list)
    for session in sessions:
        by_key[
            (session.stage, session.agent, None if session.lane is None else str(session.lane))
        ].append(session)
    by_place: dict[tuple[str | None, str | None], list[_Session]] = defaultdict(list)
    for session in sessions:
        by_place[(session.stage, None if session.lane is None else str(session.lane))].append(
            session
        )
    for event in events:
        if event.type in ("llm.call", "llm.request"):
            candidates = by_key.get(_key(event)) or by_place.get(
                (event.stage, None if event.lane is None else str(event.lane)), []
            )  # an event that names no agent still belongs to the stage's session
            owner = _by_emission(candidates, _emission(event))
            if owner is not None:
                (owner.calls if event.type == "llm.call" else owner.requests).append(event)
        elif event.type == "tool.call":
            owner = _by_time(sessions, event)
            if owner is not None:
                owner.tools.append(event)


def _by_emission(candidates: list[_Session], emission: int) -> _Session | None:
    owner: _Session | None = None
    for session in candidates:  # sessions are in start order
        if session.start_emission <= emission:
            owner = session
    return owner


def _by_time(sessions: list[_Session], event: Event) -> _Session | None:
    lane = None if event.lane is None else str(event.lane)
    open_now = [
        s
        for s in sessions
        if s.stage == event.stage
        and (None if s.lane is None else str(s.lane)) == lane
        and s.covers(event.ts)
    ]
    if not open_now:
        return None
    same_agent = [s for s in open_now if s.agent == event.agent]
    return (same_agent or open_now)[-1]


def _parts(requests: Iterable[Event]) -> tuple[dict[str, int], int]:
    """Estimated prompt tokens by part, summed over a session's requests, and the largest tool
    schema any one request carried."""

    chars = dict.fromkeys(PARTS, 0)
    widest = 0
    for request in requests:
        sent = request.data.get("prompt_chars") or {}
        for name in PARTS:
            chars[name] += int(sent.get(name) or 0)
        widest = max(widest, int(sent.get("tools") or 0))
    return {
        name: value // CHARS_PER_TOKEN for name, value in chars.items()
    }, widest // CHARS_PER_TOKEN


def build_sessions(
    events: Iterable[Event],
    prices: PriceTable,
    trajectory: Mapping[int, TrajectoryEntry] | None = None,
) -> list[SessionUsage]:
    """The run's sessions, in the order they started.

    ``trajectory`` maps ``trajectory_seq`` to its entry; the output of a failed tool call is what
    names its error kind (without it a failure is counted as ``unknown``).
    """

    log = sorted(events, key=lambda event: event.seq)
    sessions = _open_sessions(log)
    _assign(log, sessions)
    last = max((event.ts for event in log), default=None)
    found: list[SessionUsage] = []
    for session in sessions:
        ended = session.ended or last or session.started
        usages = [call.data.get("usage") or {} for call in session.calls]
        prompt = sum(int(u.get("prompt_tokens") or 0) for u in usages)
        cached = sum(int(u.get("cached_prompt_tokens") or 0) for u in usages)
        completion = sum(int(u.get("completion_tokens") or 0) for u in usages)
        model = next((str(c.data["model"]) for c in session.calls if c.data.get("model")), "")
        failures: dict[str, int] = defaultdict(int)
        for call in session.tools:
            if call.data.get("ok") is False:
                entry = (trajectory or {}).get(call.data.get("trajectory_seq", -1))
                failures[error_kind(entry.output) if entry else "unknown"] += 1
        parts, schema = _parts(session.requests)
        found.append(
            SessionUsage(
                id=session.id,
                stage=session.stage,
                agent=session.agent,
                lane=session.lane,
                task=session.task,
                model=model,
                started=session.started,
                ended=ended,
                seconds=round(max(0.0, (ended - session.started).total_seconds()), 3),
                ended_with=session.ended_with,  # type: ignore[arg-type]
                calls=len(session.calls),
                prompt_tokens=prompt,
                cached_prompt_tokens=cached,
                completion_tokens=completion,
                prompt_parts={**parts, "tool_schema_per_call": schema},
                tool_calls=len(session.tools),
                tool_failures=dict(sorted(failures.items())),
                cost_usd=(
                    estimate_cost(
                        prices.lookup(model),
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        cached_prompt_tokens=cached,
                    )
                    if model
                    else None
                ),
            )
        )
    return found


def run_sessions(run_dir: Path, prices: PriceTable) -> list[SessionUsage]:
    """The sessions of the run in ``run_dir``, read from its event log and trajectory."""

    from engineering_team.runtime.events import read_events
    from engineering_team.runtime.run_store import EVENTS_FILENAME, TRAJECTORY_FILENAME
    from engineering_team.runtime.trajectory import read_trajectory

    entries = {entry.seq: entry for entry in read_trajectory(run_dir / TRAJECTORY_FILENAME)}
    return build_sessions(read_events(run_dir / EVENTS_FILENAME), prices, entries)
