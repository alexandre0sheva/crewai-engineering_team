"""Reading a run's ``events.jsonl`` for the API: resumable tailing and an incremental digest.

One :class:`EventLog` per run directory is kept by the server. It remembers where it has read to,
so asking again costs only the new lines, and it keeps a sparse byte index (every ``STRIDE``
lines) so a client that reconnects with ``Last-Event-ID`` is served from the right place without
reading the file from the start. ``seq`` of an event is its line number in the file.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from engineering_team.contracts import Event, UsageReport
from engineering_team.pricing import PriceTable
from engineering_team.runtime.usage import UsageTracker

STRIDE = 256
MAX_TRAIL = 20_000  # tool calls kept for card trails
MAX_LINE_BYTES = 1_000_000


@dataclass(frozen=True)
class ToolRecord:
    seq: int
    ts: datetime
    agent: str
    lane: str | None
    tool: str
    ok: bool
    duration: float
    args: str


@dataclass
class AgentStats:
    calls: int = 0
    failed: int = 0
    last: ToolRecord | None = None


@dataclass(frozen=True)
class OpenQuestion:
    id: str
    text: str
    agent: str | None
    card_id: str | None
    asked: datetime


@dataclass
class Digest:
    """What a pass over the events established so far."""

    last_seq: int = 0
    last_ts: datetime | None = None
    agents: dict[str, AgentStats] = field(default_factory=dict)
    trail: deque[ToolRecord] = field(default_factory=lambda: deque(maxlen=MAX_TRAIL))
    questions: dict[str, OpenQuestion] = field(default_factory=dict)
    cancel_requested: bool = False
    finished: bool = False  # the last ``run.*`` event was ``run.finished``


def _short(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class EventLog:
    """Thread-safe reader of one run's event file."""

    def __init__(self, path: Path, prices: PriceTable) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._marks: list[tuple[int, int]] = [(0, 0)]  # (lines before offset, byte offset)
        self._offset = 0
        self._lines = 0
        self._digest = Digest()
        self._usage = UsageTracker()
        self._prices = prices

    # -- the digest -------------------------------------------------------------------------

    def digest(self) -> Digest:
        with self._lock:
            self._advance()
            return self._digest

    def usage(self) -> UsageReport:
        with self._lock:
            self._advance()
            return self._usage.report(self._prices)

    def _advance(self) -> None:
        """Read what was appended since the last call (complete lines only)."""

        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self._offset:  # the file was replaced: start over
            self._reset()
        if size == self._offset:
            return
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(size - self._offset)
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        position = self._offset
        for line in chunk[: end + 1].splitlines(keepends=True):
            self._lines += 1
            position += len(line)
            if self._lines % STRIDE == 0:
                self._marks.append((self._lines, position))
            self._absorb(line)
        self._offset += end + 1

    def _reset(self) -> None:
        self._marks, self._offset, self._lines = [(0, 0)], 0, 0
        self._digest, self._usage = Digest(), UsageTracker()

    def _absorb(self, line: bytes) -> None:
        event = _parse(line)
        if event is None:
            return
        digest, data, kind = self._digest, event.data, event.type
        digest.last_seq, digest.last_ts = max(digest.last_seq, event.seq), event.ts
        self._usage.emit(kind, stage=event.stage, agent=event.agent, **data)
        if kind == "tool.call":
            agent = event.agent or "unknown"
            record = ToolRecord(
                event.seq,
                event.ts,
                agent,
                None if event.lane is None else str(event.lane),
                str(data.get("tool", "tool")),
                bool(data.get("ok", True)),
                float(data.get("duration") or 0),
                _short(data.get("args", "")),
            )
            stats = digest.agents.setdefault(agent, AgentStats())
            stats.calls += 1
            stats.failed += 0 if record.ok else 1
            stats.last = record
            digest.trail.append(record)
        elif kind == "question":
            qid = str(data.get("question_id", ""))
            if data.get("interactive"):
                digest.questions[qid] = OpenQuestion(
                    qid, str(data.get("text", "")), event.agent, data.get("card_id"), event.ts
                )
        elif kind in ("question.answered", "question.unanswered"):
            digest.questions.pop(str(data.get("question_id", "")), None)
        elif kind == "run.cancel_requested":
            digest.cancel_requested = True
        elif kind == "run.started":
            digest.finished = False
            digest.cancel_requested = False
            digest.questions.clear()  # a resumed run's old questions are gone
        elif kind == "run.finished":
            digest.finished = True

    # -- reading events -------------------------------------------------------------------

    def read_after(self, after: int, limit: int = 500) -> list[tuple[int, bytes]]:
        """``(seq, raw JSON line)`` of up to ``limit`` events with ``seq > after``, in order."""

        with self._lock:
            self._advance()
            start = max((mark for mark in self._marks if mark[0] <= after), default=(0, 0))
            end = self._offset
        found: list[tuple[int, bytes]] = []
        try:
            with self.path.open("rb") as handle:
                handle.seek(start[1])
                while handle.tell() < end and len(found) < limit:
                    line = handle.readline(MAX_LINE_BYTES)
                    if not line.endswith(b"\n"):
                        break
                    event = _parse(line)
                    if event is not None and event.seq > after:
                        found.append((event.seq, line.strip()))
        except OSError:
            return found
        return found

    def events_upto(self, seq: int) -> list[Event]:
        """Every event with ``seq`` at most ``seq`` (the board replay folds these)."""

        events: list[Event] = []
        after = 0
        while True:
            batch = self.read_after(after, 2000)
            if not batch:
                return events
            for number, line in batch:
                if number > seq:
                    return events
                event = _parse(line)
                if event is not None:
                    events.append(event)
            after = batch[-1][0]


def _parse(line: bytes) -> Event | None:
    try:
        return Event.model_validate(json.loads(line))
    except ValueError:
        return None
