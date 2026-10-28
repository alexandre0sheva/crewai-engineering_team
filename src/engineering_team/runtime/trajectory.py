"""The trajectory: what the agents saw, one JSON line per tool call or model message.

``events.jsonl`` says *which* tool was called; ``runs/<id>/trajectory.jsonl`` keeps the (redacted,
capped) output as well, so reflection, tuning and failure analysis can read what an agent
observed. Entries are append-only; ``seq`` is the entry's line number, and ``tool.call`` events
carry it as ``trajectory_seq`` to link the two logs. Reading skips torn lines, like the event log.

Per-call output is capped at ``trajectory.max_output_chars``. Once the file reaches
``trajectory.max_mb`` everything is truncated much harder, and a few MB past that nothing more is
written (one ``notice`` entry says so); the report states it.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from engineering_team.contracts import utc_now
from engineering_team.runtime.events import Scrubber, append_bytes, current_lane, current_stage

log = logging.getLogger(__name__)

DEFAULT_OUTPUT_CHARS = 50_000
MIN_OUTPUT_CHARS = 10_000  # the cap never goes below this, whatever the setting says
MESSAGE_CHARS = 10_000
ARGUMENT_CHARS = 2_000
BUDGET_OUTPUT_CHARS = 1_000  # what an output, message or argument is cut to once over budget
STOP_FACTOR = 1.25  # past this many times the size budget nothing more is written
MEGABYTE = 1024 * 1024

Kind = Literal["tool", "message", "notice"]


class TrajectoryEntry(BaseModel):
    """One line of ``trajectory.jsonl``."""

    seq: int
    ts: datetime
    kind: Kind
    stage: str | None = None
    teammate: str | None = None
    lane: int | str | None = None
    # kind == "tool"
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    ok: bool = True
    ms: int = 0
    # kind == "message": "assistant" (a model reply) or "final" (the agent's answer);
    # kind == "notice": the text
    role: str = ""
    text: str = ""
    truncated: bool = False


def cap_text(text: str, limit: int) -> tuple[str, bool]:
    """``text`` cut to ``limit`` characters (saying how much was left out) and whether it was."""

    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}\n... [truncated {len(text) - limit} more characters]", True


def _plain(value: Any) -> Any:
    """Arguments as JSON-compatible data (objects become strings before they are scrubbed)."""

    return json.loads(json.dumps(value, default=str))


class TrajectoryRecorder:
    """Appends tool calls and model messages to the run's ``trajectory.jsonl``.

    Safe to share between threads (parallel lanes and CrewAI's handler threads): each entry is
    one ``O_APPEND`` write under a lock, and ``seq`` matches file order. An existing file is
    continued (``resume``). Recording never raises into the run.
    """

    def __init__(
        self,
        path: Path,
        *,
        scrubber: Scrubber | None = None,
        max_output_chars: int = DEFAULT_OUTPUT_CHARS,
        max_mb: float = 50,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self._scrubber = scrubber or Scrubber()
        self._output_chars = max(MIN_OUTPUT_CHARS, max_output_chars)
        self._budget = int(max_mb * MEGABYTE)
        self._clock = clock
        self._lock = threading.Lock()
        self._seq, self._bytes = self._existing()
        self._squeezing = self._bytes >= self._budget  # a resumed run already over budget
        self._stopped = self._bytes >= self._budget * STOP_FACTOR

    def _existing(self) -> tuple[int, int]:
        try:
            with self.path.open("rb") as handle:
                lines = sum(1 for _ in handle)
            return lines, self.path.stat().st_size
        except OSError:
            return 0, 0

    @property
    def over_budget(self) -> bool:
        return self._bytes >= self._budget

    def tool_call(
        self,
        *,
        tool: str,
        args: Mapping[str, object],
        output: str,
        ok: bool,
        ms: int,
        teammate: str | None = None,
        lane: int | str | None = None,
        stage: str | None = None,
        ts: datetime | None = None,
    ) -> int | None:
        """Record one tool call; returns its ``seq``, or ``None`` when nothing could be written.

        ``ts`` is when the call began, so the trace orders it after the model reply that asked
        for it.
        """

        return self._record(
            "tool",
            ts,
            stage,
            teammate,
            lane,
            tool=tool,
            args=_plain(dict(args)),
            output=output,
            ok=ok,
            ms=ms,
        )

    def message(
        self,
        *,
        role: str,
        text: str,
        teammate: str | None = None,
        lane: int | str | None = None,
        stage: str | None = None,
        ts: datetime | None = None,
    ) -> int | None:
        """Record what a model said: a reply (``assistant``) or an agent's answer (``final``)."""

        if not text.strip():
            return None
        return self._record("message", ts, stage, teammate, lane, role=role, text=text)

    def _record(
        self,
        kind: Kind,
        ts: datetime | None,
        stage: str | None,
        teammate: str | None,
        lane: int | str | None,
        **fields: Any,
    ) -> int | None:
        try:
            entry = self._build(kind, ts, stage, teammate, lane, fields)
            with self._lock:
                if self._stopped:
                    return None
                if self._bytes >= self._budget * STOP_FACTOR:
                    self._stopped = True
                    self._write_locked(self._notice("recording stopped: the trajectory is full"))
                    return None
                if self._bytes >= self._budget:
                    if not self._squeezing:
                        self._squeezing = True
                        self._write_locked(
                            self._notice(
                                f"size budget reached ({self._budget // MEGABYTE} MB): later "
                                f"outputs are cut to {BUDGET_OUTPUT_CHARS} characters"
                            )
                        )
                    entry = self._squeeze(entry)
                return self._write_locked(entry)
        except (OSError, TypeError, ValueError):
            log.warning("Could not record a trajectory entry in %s", self.path, exc_info=True)
            return None

    def _build(
        self,
        kind: Kind,
        ts: datetime | None,
        stage: str | None,
        teammate: str | None,
        lane: int | str | None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        # Scrub before cutting: a cut through a secret would leave a prefix nothing recognises.
        scrubbed = self._scrubber.scrub(fields)
        truncated = False
        if kind == "tool":
            scrubbed["output"], truncated = cap_text(str(scrubbed["output"]), self._output_chars)
            scrubbed["args"], cut = self._cap_arguments(scrubbed["args"], ARGUMENT_CHARS)
            truncated = truncated or cut
        else:
            scrubbed["text"], truncated = cap_text(str(scrubbed["text"]), MESSAGE_CHARS)
        lane = current_lane.get() if lane is None else lane
        return {
            "ts": (ts or self._clock()).isoformat(),
            "kind": kind,
            "stage": stage or current_stage.get(),
            "teammate": teammate,
            "lane": lane,
            **scrubbed,
            "truncated": truncated,
        }

    @staticmethod
    def _cap_arguments(args: Mapping[str, Any], limit: int) -> tuple[dict[str, Any], bool]:
        capped: dict[str, Any] = {}
        cut = False
        for name, value in args.items():
            if isinstance(value, str):
                value, was_cut = cap_text(value, limit)
                cut = cut or was_cut
            capped[name] = value
        return capped, cut

    def _squeeze(self, entry: dict[str, Any]) -> dict[str, Any]:
        """The same entry cut to the over-budget limit."""

        entry = dict(entry)
        if entry["kind"] == "tool":
            entry["output"], cut = cap_text(entry["output"], BUDGET_OUTPUT_CHARS)
            entry["args"], cut_args = self._cap_arguments(entry["args"], BUDGET_OUTPUT_CHARS)
            cut = cut or cut_args
        else:
            entry["text"], cut = cap_text(entry["text"], BUDGET_OUTPUT_CHARS)
        entry["truncated"] = entry["truncated"] or cut
        return entry

    def _notice(self, text: str) -> dict[str, Any]:
        return {"ts": self._clock().isoformat(), "kind": "notice", "text": text, "truncated": False}

    def _write_locked(self, entry: dict[str, Any]) -> int:
        seq = self._seq + 1
        line = json.dumps({"seq": seq, **entry}, ensure_ascii=False, separators=(",", ":")) + "\n"
        payload = line.encode("utf-8")
        append_bytes(self.path, payload)
        self._seq = seq
        self._bytes += len(payload)
        return seq


def read_trajectory(path: Path) -> Iterator[TrajectoryEntry]:
    """Entries of a trajectory file in file order, skipping blank and torn lines."""

    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield TrajectoryEntry.model_validate_json(line)
            except ValueError:
                continue


@dataclass(frozen=True)
class TrajectorySummary:
    """What a trajectory file holds, without loading it: for the report."""

    entries: int
    size: int
    notices: list[str]


def summarize(path: Path) -> TrajectorySummary | None:
    """Entry count, size and notices of a trajectory file; ``None`` when the run has none."""

    try:
        size = path.stat().st_size
        handle = path.open(encoding="utf-8")
    except OSError:
        return None
    entries = 0
    notices: list[str] = []
    with handle:
        for line in handle:
            if not line.strip():
                continue
            entries += 1
            if '"kind":"notice"' in line:
                try:
                    notices.append(TrajectoryEntry.model_validate_json(line).text)
                except ValueError:
                    continue
    return TrajectorySummary(entries, size, notices)


def read_entry(path: Path, seq: int) -> TrajectoryEntry | None:
    """The entry with this ``seq`` (its line number), or ``None``."""

    for entry in read_trajectory(path):
        if entry.seq == seq:
            return entry
        if entry.seq > seq:
            break
    return None
