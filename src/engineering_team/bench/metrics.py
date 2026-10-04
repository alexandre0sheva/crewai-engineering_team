"""What a finished run left behind: the team's JSON summary and its event log."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engineering_team.pricing import build_table
from engineering_team.runtime.events import read_events
from engineering_team.runtime.usage import UsageTracker


def parse_summary(path: Path) -> dict[str, Any]:
    """The JSON document the team printed on stdout (``{}`` when it printed none).

    ``--json`` makes the summary the last thing on stdout, starting at the beginning of a line; a
    team that also printed other text must not confuse it, so candidates are tried from the end
    and the first object that looks like a summary wins.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    decoder = json.JSONDecoder()
    starts = [0] if text.startswith("{") else []
    starts += [i + 1 for i, char in enumerate(text) if char == "\n" and text[i + 1 : i + 2] == "{"]
    for start in reversed(starts[-50:]):
        with contextlib.suppress(ValueError):
            value, _ = decoder.raw_decode(text, start)
            if isinstance(value, dict) and ("status" in value or "run_id" in value):
                return value
    return {}


@dataclass
class EventMetrics:
    repair_rounds: int = 0
    tool_failures: int = 0
    setup_failures: int = 0
    models: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def event_metrics(events_file: Path) -> EventMetrics:
    """What the run's own event log says: repairs, failed tool calls, failed setup checks, models,
    and (the ground truth even for a crashed run) token usage and cost."""

    metrics = EventMetrics()
    setup_ids: set[str] = set()
    models: set[str] = set()
    events = list(read_events(events_file))
    for event in events:
        data = event.data
        if event.type == "verify.repair":
            metrics.repair_rounds += 1
        elif event.type == "tool.call" and data.get("ok") is False:
            metrics.tool_failures += 1
        elif event.type == "check.started" and data.get("kind") == "setup":
            setup_ids.add(str(data.get("check")))
        elif (
            event.type == "check.finished"
            and str(data.get("check")) in setup_ids
            and data.get("status") in ("failed", "unavailable")
        ):
            metrics.setup_failures += 1
        elif event.type == "llm.call" and data.get("model"):
            models.add(str(data["model"]))
    metrics.models = sorted(models)
    if events:
        report = UsageTracker.from_events(events).report(build_table())
        metrics.usage = {
            "tokens": report.totals.total_tokens,
            "prompt_tokens": report.totals.prompt_tokens,
            "completion_tokens": report.totals.completion_tokens,
            "model_calls": report.totals.calls,
            "tool_calls": report.tool_calls,
            "estimated_cost_usd": report.estimated_cost_usd,
        }
    return metrics
