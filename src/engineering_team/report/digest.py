"""One pass over ``events.jsonl``: everything the report needs from it.

The log can be large (every tool call is a line), so the report reads it once and keeps only
counts, lane intervals, screenshot records, and the few events that explain how a run ended.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from engineering_team.contracts import Event
from engineering_team.runtime.events import read_events

MAX_NOTICES = 20
MAX_TEXT = 300


@dataclass
class ToolStats:
    calls: int = 0
    failed: int = 0
    seconds: float = 0.0
    by_tool: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class LaneSpan:
    stage: str
    lane: str
    unit: str
    start: datetime
    end: datetime | None
    status: str
    error: str = ""


@dataclass(frozen=True)
class ShotRecord:
    path: str
    agent: str
    url: str
    size: int


@dataclass
class EventDigest:
    tools: dict[str, ToolStats] = field(default_factory=lambda: defaultdict(ToolStats))
    lanes: list[LaneSpan] = field(default_factory=list)
    screenshots: list[ShotRecord] = field(default_factory=list)
    notices: list[tuple[str, str]] = field(default_factory=list)  # (tone, text)
    error: str = ""  # the ``run.finished`` error, when the run failed
    last_ts: datetime | None = None
    repair_rounds: int = 0


def _text(value: object) -> str:
    return " ".join(str(value).split())[:MAX_TEXT]


def _notice(event: Event) -> tuple[str, str] | None:
    data = event.data
    kind = event.type
    if kind == "budget.warning":
        return "warn", f"Budget: {_text(data.get('note') or data.get('limit') or 'warning')}"
    if kind == "git.warning":
        return "warn", f"Git: {_text(data.get('error', ''))}"
    if kind == "pipeline.board_warning":
        return "info", f"The task board could not be updated: {_text(data.get('error', ''))}"
    if kind == "fix.unreproduced":
        return "warn", f"The bug was not reproduced after {data.get('attempts')} attempt(s)."
    if kind == "parallel.optional_missing":
        return "warn", f"Optional work packages did not finish: {_text(data.get('packages'))}"
    if kind == "inbox.rejected":
        return "info", f"A command sent to the run was rejected: {_text(data.get('reason', ''))}"
    return None


def _safe_screenshot(path: object) -> str | None:
    """``screenshots/<name>.png`` only: the event is data, never a path to follow."""

    pure = PurePosixPath(str(path))
    if pure.parts[:1] != ("screenshots",) or len(pure.parts) != 2 or pure.suffix != ".png":
        return None
    return str(pure)


def digest_events(path: Path) -> EventDigest:
    digest = EventDigest()
    open_lanes: dict[tuple[str, str, str], Event] = {}
    for event in read_events(path):
        digest.last_ts = event.ts
        data, kind = event.data, event.type
        if kind == "tool.call":
            stats = digest.tools[event.agent or "unknown"]
            stats.calls += 1
            stats.failed += 0 if data.get("ok", True) else 1
            stats.seconds += float(data.get("duration") or 0)
            stats.by_tool[str(data.get("tool", "tool"))] += 1
        elif kind == "lane.started":
            key = (event.stage or "", str(event.lane), str(data.get("unit", "")))
            open_lanes[key] = event
        elif kind == "lane.finished":
            key = (event.stage or "", str(event.lane), str(data.get("unit", "")))
            began = open_lanes.pop(key, None)
            digest.lanes.append(
                LaneSpan(
                    key[0],
                    key[1],
                    key[2],
                    (began or event).ts,
                    event.ts,
                    str(data.get("status", "")),
                    _text(data.get("error", "")),
                )
            )
        elif kind == "artifact.created" and data.get("kind") == "screenshot":
            if (relative := _safe_screenshot(data.get("path"))) is not None:
                digest.screenshots.append(
                    ShotRecord(
                        relative,
                        str(event.agent or ""),
                        str(data.get("url") or ""),
                        int(data.get("bytes") or 0),
                    )
                )
        elif kind == "verify.repair":
            digest.repair_rounds += 1
        elif kind == "run.finished":
            digest.error = _text(data.get("error", ""))
        elif (notice := _notice(event)) is not None and len(digest.notices) < MAX_NOTICES:
            digest.notices.append(notice)
    for (stage, lane, unit), began in open_lanes.items():  # a lane the run never closed
        digest.lanes.append(LaneSpan(stage, lane, unit, began.ts, None, "interrupted"))
    return digest
