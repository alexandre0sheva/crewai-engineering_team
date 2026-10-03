"""Structured run events: the sink protocol, the JSONL log, fan-out, and secret scrubbing.

Anything that happens during a run is reported with ``sink.emit(type, **data)``. The default
sink for a run is a :class:`JsonlSink` writing ``runs/<run_id>/events.jsonl``; the UI and CLI
read the same file. Sinks never raise into the caller: telemetry must not break a run.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from engineering_team.contracts import Event, utc_now

log = logging.getLogger(__name__)

REDACTED = "[REDACTED]"
# Shorter values ("1", "true", "dev") would blank out ordinary text; real keys are longer.
MIN_SECRET_LENGTH = 8


# The stage the current context is working in. Events emitted without an explicit ``stage`` get
# it, including events CrewAI delivers from its handler threads (they run on a copy of the
# emitter's context).
current_stage: ContextVar[str | None] = ContextVar("engineering_team_stage", default=None)


# The parallel lane the current context works in, tagged on events like the stage is.
current_lane: ContextVar[int | str | None] = ContextVar("engineering_team_lane", default=None)


@contextlib.contextmanager
def lane_scope(lane: int | str) -> Iterator[None]:
    token = current_lane.set(lane)
    try:
        yield
    finally:
        current_lane.reset(token)


@contextlib.contextmanager
def stage_scope(name: str) -> Iterator[None]:
    token = current_stage.set(name)
    try:
        yield
    finally:
        current_stage.reset(token)


class EventSink(Protocol):
    """Receives structured run events, e.g. ``emit("tool.call", tool=..., ok=True)``.

    ``stage``, ``agent`` and ``lane`` keyword arguments are lifted into the event's own
    fields; everything else becomes ``data``.
    """

    def emit(self, type: str, **data: Any) -> None: ...


class NullSink:
    """Discards every event."""

    def emit(self, type: str, **data: Any) -> None:
        return None


class Scrubber:
    """Replaces known secret values anywhere in an event with ``[REDACTED]``."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first, so a secret that contains another is replaced whole.
        self._secrets = sorted(
            {value for value in secrets if len(value) >= MIN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )

    def __bool__(self) -> bool:
        return bool(self._secrets)

    def scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, REDACTED)
            return value
        if isinstance(value, Mapping):
            return {self.scrub(str(key)): self.scrub(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self.scrub(item) for item in value]
        return value


def _jsonable(data: Mapping[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON so arbitrary objects become strings *before* scrubbing."""

    return json.loads(json.dumps(data, default=str))


class JsonlSink:
    """Appends one JSON object per line to ``path``; safe to share between threads.

    Each event is serialised, scrubbed, and written with a single ``write`` on an
    ``O_APPEND`` descriptor under a lock, so lines never interleave and ``seq`` matches file
    order. An existing file is continued (``seq`` resumes after its last line).
    """

    def __init__(
        self,
        path: Path,
        run_id: str,
        *,
        scrubber: Scrubber | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self._scrubber = scrubber or Scrubber()
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = self._existing_lines()

    def _existing_lines(self) -> int:
        try:
            with self.path.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    def emit(self, type: str, **data: Any) -> None:
        try:
            stage = data.pop("stage", None) or current_stage.get()
            agent = data.pop("agent", None)
            lane = data.pop("lane", None)
            lane = current_lane.get() if lane is None else lane
            with self._lock:
                event = Event(
                    seq=self._seq + 1,
                    ts=self._clock(),
                    run_id=self.run_id,
                    type=type,
                    stage=stage,
                    agent=agent,
                    lane=lane,
                    data=_jsonable(data),
                )
                payload = self._scrubber.scrub(event.model_dump(mode="json"))
                line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                self._append(line.encode("utf-8"))
                self._seq += 1
        except (OSError, TypeError, ValueError):
            log.warning("Could not record event %r in %s", type, self.path, exc_info=True)

    def _append(self, payload: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(descriptor, view) :]
        finally:
            os.close(descriptor)


class FanoutSink:
    """Sends every event to several sinks; one failing sink never blocks the others."""

    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = list(sinks)

    def add(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def emit(self, type: str, **data: Any) -> None:
        for sink in list(self._sinks):
            try:
                sink.emit(type, **dict(data))
            except Exception:
                log.warning("Event sink %r failed for %r", sink, type, exc_info=True)


def read_events(path: Path) -> Iterator[Event]:
    """Events from a log, skipping blank, torn, or unparseable lines (a crash can cut the last)."""

    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield Event.model_validate_json(line)
            except ValueError:
                continue
