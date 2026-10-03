"""What a run looks like right now, read from its run directory.

``RunWatcher`` is the one source for every view: the live display of a run in this process, the
plain log, ``board --watch`` on a run in another process, and ``status``. It reads
``board.json`` and ``manifest.json`` (both written atomically) and tails ``events.jsonl`` from
where it last stopped, so a poll costs only what is new.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engineering_team.board.models import BoardProgress, BoardState, Card
from engineering_team.board.progress import compute_progress
from engineering_team.contracts import Event, RunManifest, UsageReport, utc_now
from engineering_team.pricing import PriceTable
from engineering_team.runtime.run_store import EVENTS_FILENAME, MANIFEST_FILENAME
from engineering_team.runtime.usage import UsageTracker

FEED_LENGTH = 200
BOARD_FILENAME = "board.json"


@dataclass(frozen=True)
class FeedLine:
    ts: datetime
    text: str
    level: str = "info"  # info | good | warn | bad


@dataclass(frozen=True)
class Lane:
    """One parallel unit of work and the latest thing it did."""

    lane: str
    unit: str
    started: datetime
    last_tool: str = ""
    agent: str = ""


@dataclass
class ViewState:
    run_id: str
    manifest: RunManifest | None
    cards: list[Card]
    paused: bool
    progress: BoardProgress
    usage: UsageReport
    lanes: list[Lane]
    feed: list[FeedLine]
    now: datetime
    stage: str = ""
    cancel_requested: bool = False

    @property
    def status(self) -> str:
        return self.manifest.status if self.manifest else "unknown"

    @property
    def running(self) -> bool:
        return self.status in ("pending", "running")

    @property
    def elapsed_seconds(self) -> float:
        if self.manifest is None:
            return 0.0
        end = self.manifest.finished if not self.running and self.manifest.finished else self.now
        return max(0.0, (end - self.manifest.created).total_seconds())


def _short(value: object, limit: int = 70) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_event(event: Event) -> FeedLine | None:
    """One line for the activity feed, or ``None`` for events nobody needs to read."""

    data, agent, lane = event.data, event.agent, event.lane
    who = f"{agent} " if agent else ""
    where = f"[lane {lane}] " if lane is not None else ""
    kind = event.type
    ts = event.ts
    if kind == "stage.started":
        return FeedLine(ts, f"stage {event.stage} started", "info")
    if kind == "stage.finished":
        status = str(data.get("status", ""))
        level = "good" if status == "succeeded" else "bad" if status == "failed" else "warn"
        return FeedLine(ts, f"stage {event.stage} {status}", level)
    if kind in ("stage.skipped", "stage.reused"):
        return FeedLine(ts, f"stage {event.stage} {kind.split('.')[1]}: {data.get('reason', '')}")
    if kind == "lane.started":
        return FeedLine(ts, f"lane {lane} started {data.get('unit', '')}")
    if kind == "lane.finished":
        status = str(data.get("status", ""))
        level = "good" if status == "succeeded" else "bad"
        return FeedLine(ts, f"lane {lane} finished {data.get('unit', '')}: {status}", level)
    if kind == "tool.call":
        ok = data.get("ok", True)
        return FeedLine(
            ts,
            f"{where}{who}{data.get('tool', 'tool')}{'' if ok else ' (error)'}",
            "info" if ok else "warn",
        )
    if kind == "board.card_moved":
        return FeedLine(
            ts,
            f"{data.get('card_id')} {data.get('from_status')} → {data.get('to_status')}"
            + (f": {_short(data.get('reason'))}" if data.get("reason") else ""),
            "bad" if data.get("to_status") == "failed" else "info",
        )
    if kind == "check.started":
        return FeedLine(ts, f"check {data.get('check')} running")
    if kind == "check.finished":
        status = str(data.get("status", ""))
        level = (
            "good"
            if status == "passed"
            else "warn"
            if status in ("skipped", "unavailable")
            else "bad"
        )
        return FeedLine(ts, f"check {data.get('check')} {status}", level)
    if kind == "verify.repair":
        return FeedLine(ts, f"repair round {data.get('round')} for {data.get('checks')}", "warn")
    if kind == "git.checkpoint":
        return FeedLine(
            ts, f"git checkpoint {str(data.get('sha', ''))[:8]}: {_short(data.get('message'))}"
        )
    if kind == "budget.warning":
        return FeedLine(ts, f"budget warning: {_short(data.get('note'))}", "warn")
    if kind == "board.steering_delivered":
        return FeedLine(ts, f"{agent}: received {data.get('notes')} note(s) from you")
    if kind in ("board.paused", "board.unpaused"):
        return FeedLine(ts, "run " + ("paused" if kind == "board.paused" else "resumed"), "warn")
    if kind == "run.cancel_requested":
        return FeedLine(ts, f"cancel requested ({data.get('via', '')})", "warn")
    if kind == "run.started":
        return FeedLine(ts, "run started")
    if kind == "run.finished":
        status = str(data.get("status", ""))
        return FeedLine(ts, f"run {status}", "good" if status == "succeeded" else "bad")
    if kind == "question":
        return FeedLine(
            ts, f"{who}asks: {_short(data.get('question') or data.get('text'))}", "warn"
        )
    if kind == "inbox.rejected":
        return FeedLine(ts, f"a command was ignored: {data.get('reason')}", "warn")
    return None


class RunWatcher:
    """Reads one run directory; call :meth:`poll` as often as you like."""

    def __init__(
        self,
        run_dir: Path,
        prices: PriceTable,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.run_dir = run_dir
        self._prices = prices
        self._clock = clock
        self._offset = 0
        self._usage = UsageTracker()
        self._feed: deque[FeedLine] = deque(maxlen=FEED_LENGTH)
        self._lanes: dict[str, Lane] = {}
        self._stage = ""
        self._cancel_requested = False
        self.new_lines: list[FeedLine] = []
        self._lock = threading.Lock()

    def poll(self) -> ViewState:
        """Read what changed since the last poll and return the current view."""

        with self._lock:
            return self._poll()

    def _poll(self) -> ViewState:
        self.new_lines = []
        for event in self._read_new_events():
            self._absorb(event)
        manifest = self._read_manifest()
        board = self._read_board()
        now = self._clock()
        return ViewState(
            run_id=manifest.run_id if manifest else self.run_dir.name,
            manifest=manifest,
            cards=board.cards,
            paused=board.paused,
            progress=compute_progress(board.cards, now),
            usage=self._usage.report(self._prices),
            lanes=sorted(self._lanes.values(), key=lambda lane: lane.lane),
            feed=list(self._feed),
            now=now,
            stage=self._stage,
            cancel_requested=self._cancel_requested,
        )

    # -- reading -----------------------------------------------------------------------------

    def _read_new_events(self) -> list[Event]:
        path = self.run_dir / EVENTS_FILENAME
        try:
            with path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:  # nothing complete yet: a line is still being written
            return []
        self._offset += end + 1
        events: list[Event] = []
        for line in chunk[: end + 1].splitlines():
            try:
                events.append(Event.model_validate(json.loads(line)))
            except (ValueError, ValidationError):
                continue
        return events

    def _read_manifest(self) -> RunManifest | None:
        try:
            return RunManifest.model_validate_json(
                (self.run_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def _read_board(self) -> BoardState:
        try:
            return BoardState.model_validate_json(
                (self.run_dir / BOARD_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return BoardState()

    # -- absorbing events --------------------------------------------------------------------

    def _absorb(self, event: Event) -> None:
        self._usage.emit(event.type, stage=event.stage, agent=event.agent, **event.data)
        kind, lane = event.type, event.lane
        if kind == "stage.started":
            self._stage = event.stage or ""
        elif kind == "run.cancel_requested":
            self._cancel_requested = True
        elif kind == "lane.started" and lane is not None:
            self._lanes[str(lane)] = Lane(str(lane), str(event.data.get("unit", "")), event.ts)
        elif kind == "lane.finished" and lane is not None:
            self._lanes.pop(str(lane), None)
        elif kind == "tool.call" and lane is not None and str(lane) in self._lanes:
            current = self._lanes[str(lane)]
            self._lanes[str(lane)] = Lane(
                current.lane,
                current.unit,
                current.started,
                str(event.data.get("tool", "")),
                event.agent or current.agent,
            )
        line = describe_event(event)
        if line is not None:
            self._feed.append(line)
            self.new_lines.append(line)


def data_of(state: ViewState) -> dict[str, Any]:
    """The state as plain JSON data (for ``status --json`` and ``board --json``)."""

    manifest = state.manifest
    return {
        "run_id": state.run_id,
        "status": state.status,
        "stage": state.stage,
        "paused": state.paused,
        "progress": state.progress.model_dump(mode="json", exclude={"schema_version"}),
        "cards": [
            card.model_dump(
                mode="json",
                include={
                    "id",
                    "title",
                    "kind",
                    "status",
                    "assignee",
                    "lane",
                    "stage",
                    "blocked_reason",
                    "attempts",
                },
            )
            for card in state.cards
            if card.kind != "user_note"
        ],
        "usage": {
            "tokens": state.usage.totals.total_tokens,
            "calls": state.usage.totals.calls,
            "tool_calls": state.usage.tool_calls,
            "estimated_cost_usd": state.usage.estimated_cost_usd,
        },
        "elapsed_seconds": round(state.elapsed_seconds, 1),
        "stages": [
            {"name": stage.name, "status": stage.status, "attempts": stage.attempts}
            for stage in (manifest.stages if manifest else [])
        ],
    }
