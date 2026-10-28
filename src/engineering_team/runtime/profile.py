"""The run profile: where the time and the money went, from a run's recorded sessions.

``build_profile`` reads ``events.jsonl`` and ``trajectory.jsonl`` (both optional: a run from
before 0.3.0 has no ``llm.request`` events and no trajectory, and still gets what it has), and
``engineering-team profile`` and the report print it. Definitions:

* *critical path*: the longest chain of sessions that ran one after another (each starting after
  the previous ended), by session time. With sessions that never overlap it is all of them;
  parallel lanes shorten it.
* *sequential stages*: stages that ran several sessions strictly one after another. They are
  candidates for running in parallel, not proof that they could.
* *re-read files*: paths a read tool fetched more than once in the run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.contracts import SessionUsage
from engineering_team.pricing import PriceTable
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import EVENTS_FILENAME, TRAJECTORY_FILENAME
from engineering_team.runtime.sessions import build_sessions
from engineering_team.runtime.trajectory import TrajectoryEntry, read_trajectory

READ_TOOLS = ("Read Project File", "Read File Range")
TOP = 10
MAX_MESSAGE_CHARS = 160


@dataclass(frozen=True)
class SequentialStage:
    stage: str
    sessions: int
    seconds: float


@dataclass(frozen=True)
class Reread:
    path: str
    reads: int
    sessions: int  # in how many different agent sessions it was read


@dataclass(frozen=True)
class FailureNote:
    tool: str
    kind: str
    message: str
    count: int


@dataclass
class Profile:
    run_id: str
    elapsed_seconds: float
    sessions: list[SessionUsage]
    critical_path: list[str] = field(default_factory=list)
    critical_seconds: float = 0.0
    sequential_stages: list[SequentialStage] = field(default_factory=list)
    rereads: list[Reread] = field(default_factory=list)
    failures: list[FailureNote] = field(default_factory=list)
    recorded_prompts: bool = False  # the run logged prompt sizes (``llm.request`` events)

    @property
    def session_seconds(self) -> float:
        return round(sum(s.seconds for s in self.sessions), 3)

    @property
    def parallelism(self) -> float:
        """Session time per second of elapsed time: above 1 only when sessions overlapped."""

        return (
            round(self.session_seconds / self.elapsed_seconds, 2) if self.elapsed_seconds else 0.0
        )

    @property
    def calls(self) -> int:
        return sum(s.calls for s in self.sessions)

    @property
    def prompt_tokens(self) -> int:
        return sum(s.prompt_tokens for s in self.sessions)

    @property
    def cached_share(self) -> float:
        return (
            sum(s.cached_prompt_tokens for s in self.sessions) / self.prompt_tokens
            if self.prompt_tokens
            else 0.0
        )

    @property
    def cost_usd(self) -> float | None:
        costs = [s.cost_usd for s in self.sessions]
        return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)

    def schema_tokens(self) -> dict[str, int]:
        """The tool-schema tokens each agent sends with every model call (its largest session)."""

        found: dict[str, int] = {}
        for session in self.sessions:
            tokens = session.prompt_parts.get("tool_schema_per_call", 0)
            name = session.agent or "unknown"
            found[name] = max(found.get(name, 0), tokens)
        return dict(sorted(found.items(), key=lambda item: -item[1]))


def critical_path(sessions: list[SessionUsage]) -> tuple[list[str], float]:
    """The longest chain of sessions that ran one after another, and its session time."""

    ordered = sorted(sessions, key=lambda s: (s.started, s.ended))
    best: dict[str, tuple[float, list[str]]] = {}
    for index, session in enumerate(ordered):
        before = [
            best[earlier.id]
            for earlier in ordered[:index]
            if earlier.ended <= session.started and earlier.id in best
        ]
        length, chain = max(before, default=(0.0, []), key=lambda item: item[0])
        best[session.id] = (length + session.seconds, [*chain, session.id])
    if not best:
        return [], 0.0
    length, chain = max(best.values(), key=lambda item: item[0])
    return chain, round(length, 3)


def sequential_stages(sessions: list[SessionUsage]) -> list[SequentialStage]:
    """Stages with several sessions of which none overlapped another."""

    by_stage: dict[str, list[SessionUsage]] = defaultdict(list)
    for session in sessions:
        by_stage[session.stage or "unstaged"].append(session)
    found: list[SequentialStage] = []
    for stage, group in by_stage.items():
        group.sort(key=lambda s: s.started)
        if len(group) > 1 and all(
            a.ended <= b.started for a, b in zip(group, group[1:], strict=False)
        ):
            found.append(
                SequentialStage(stage, len(group), round(sum(s.seconds for s in group), 3))
            )
    return sorted(found, key=lambda item: -item.seconds)


def rereads(entries: list[TrajectoryEntry]) -> list[Reread]:
    reads: Counter[str] = Counter()
    seen_in: dict[str, set[tuple[str | None, str | None, int | str | None]]] = defaultdict(set)
    for entry in entries:
        path = entry.args.get("path") if entry.kind == "tool" and entry.tool in READ_TOOLS else None
        if isinstance(path, str) and path:
            reads[path] += 1
            seen_in[path].add((entry.stage, entry.teammate, entry.lane))
    found = [Reread(path, count, len(seen_in[path])) for path, count in reads.items() if count > 1]
    return sorted(found, key=lambda item: (-item.reads, item.path))[:TOP]


def failures(entries: list[TrajectoryEntry]) -> list[FailureNote]:
    from engineering_team.runtime.sessions import error_kind

    counts: Counter[tuple[str, str, str]] = Counter()
    for entry in entries:
        if entry.kind == "tool" and not entry.ok:
            message = entry.output.removeprefix("ERROR:").strip().splitlines()[:1]
            text = (message[0] if message else "")[:MAX_MESSAGE_CHARS]
            counts[(entry.tool, error_kind(entry.output), text)] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [FailureNote(tool, kind, text, count) for (tool, kind, text), count in ranked[:TOP]]


def build_profile(run_dir: Path, prices: PriceTable) -> Profile:
    """The profile of the run in ``run_dir`` (empty sessions for a run that logged none)."""

    events = list(read_events(run_dir / EVENTS_FILENAME))
    entries = list(read_trajectory(run_dir / TRAJECTORY_FILENAME))
    sessions = build_sessions(events, prices, {entry.seq: entry for entry in entries})
    elapsed = (events[-1].ts - events[0].ts).total_seconds() if len(events) > 1 else 0.0
    path, length = critical_path(sessions)
    return Profile(
        run_id=run_dir.name,
        elapsed_seconds=round(elapsed, 3),
        sessions=sessions,
        critical_path=path,
        critical_seconds=length,
        sequential_stages=sequential_stages(sessions),
        rereads=rereads(entries),
        failures=failures(entries),
        recorded_prompts=any(event.type == "llm.request" for event in events),
    )
