"""Build a run directory by hand, with exactly the files a test wants the report to read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from engineering_team.atomic_io import atomic_write_json
from engineering_team.board.models import BoardState, Card, Move
from engineering_team.contracts import (
    BudgetLimitStatus,
    BudgetStatus,
    CheckResult,
    CriterionCoverage,
    Finding,
    RunManifest,
    RunSummary,
    StageRecord,
    UsageReport,
    UsageTotals,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import JsonlSink

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
RUN_ID = "20261003-120000-abc123"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run_dir_in(root: Path, run_id: str = RUN_ID) -> Path:
    path = root / "ws" / "demo" / ".engineering-team" / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_manifest(run_dir: Path, **fields: object) -> RunManifest:
    values: dict[str, object] = {
        "run_id": run_dir.name,
        "project_name": "demo",
        "status": "succeeded",
        "strategy": "pipeline",
        "recipe": "default",
        "created": T0,
        "finished": at(120),
        "versions": {"engineering_team": "0.2.0", "crewai": "1.15.0", "python": "3.12.1"},
        "stages": [
            StageRecord(
                name="spec", status="succeeded", started=at(0), finished=at(20), attempts=1
            ),
            StageRecord(
                name="implement", status="succeeded", started=at(20), finished=at(90), attempts=1
            ),
            StageRecord(name="verify", status="succeeded", started=at(90), finished=at(120)),
        ],
    }
    values.update(fields)
    manifest = RunManifest.model_validate(values)
    atomic_write_json(run_dir / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def write_events(run_dir: Path, events: list[Event]) -> None:
    """``(seconds after start, type, fields)``; ``stage``/``agent``/``lane`` are lifted out."""

    clock = {"now": T0}
    sink = JsonlSink(run_dir / "events.jsonl", run_dir.name, clock=lambda: clock["now"])
    for seconds, kind, fields in events:
        clock["now"] = at(seconds)
        sink.emit(kind, **fields)


Event = tuple[float, str, dict[str, object]]


def tool_call(
    seconds: float, agent: str, tool: str, ok: bool = True, duration: float = 0.1
) -> Event:
    return (seconds, "tool.call", {"agent": agent, "tool": tool, "ok": ok, "duration": duration})


def lane_started(seconds: float, lane: int, unit: str, stage: str = "implement") -> Event:
    return (seconds, "lane.started", {"stage": stage, "lane": lane, "unit": unit})


def lane_finished(
    seconds: float, lane: int, unit: str, status: str = "succeeded", error: str = ""
) -> Event:
    fields: dict[str, object] = {"stage": "implement", "lane": lane, "unit": unit, "status": status}
    if error:
        fields["error"] = error
    return (seconds, "lane.finished", fields)


def screenshot_event(seconds: float, path: str, **fields: object) -> Event:
    return (seconds, "artifact.created", {"kind": "screenshot", "path": path, **fields})


def write_board(run_dir: Path, cards: list[Card]) -> None:
    state = BoardState(run_id=run_dir.name, counter=len(cards), cards=cards)
    atomic_write_json(run_dir / "board.json", state.model_dump(mode="json"))


def card(card_id: str, title: str, status: str = "done", **fields: object) -> Card:
    moves = [
        Move(ts=at(1), actor="controller", to_status="backlog"),
        Move(ts=at(30), actor="backend", from_status="backlog", to_status="in_progress"),
        Move(ts=at(80), actor="controller", from_status="in_progress", to_status=status),  # type: ignore[arg-type]
    ]
    return Card.model_validate(
        {
            "id": card_id,
            "title": title,
            "kind": "work_package",
            "status": status,
            "assignee": "backend",
            "history": moves,
            **fields,
        }
    )


def write_state(run_dir: Path, **fields: object) -> PipelineState:
    state = PipelineState.model_validate(fields)
    state.save(run_dir)
    return state


def check(check_id: str = "tests", status: str = "passed", **fields: object) -> CheckResult:
    return CheckResult.model_validate(
        {
            "id": check_id,
            "name": f"{check_id} check",
            "status": status,
            "kind": "test",
            "command": "pytest -q",
            "exit_code": 0 if status == "passed" else 1,
            "duration": 1.5,
            "summary": "ok" if status == "passed" else "2 failed",
            **fields,
        }
    )


def write_usage(run_dir: Path, *, cost: float | None = 0.25, tool_calls: int = 5) -> None:
    report = UsageReport(
        totals=UsageTotals(prompt_tokens=900, completion_tokens=100, total_tokens=1000, calls=4),
        tool_calls=tool_calls,
        estimated_cost_usd=cost,
        known_cost_usd=cost or 0.0,
        unpriced_models=[] if cost is not None else ["odd/model"],
        by_stage={"spec": UsageTotals(total_tokens=400, calls=1)},
        by_agent={"backend": UsageTotals(total_tokens=600, calls=3)},
        cost_by_model={"openai/gpt-x": cost},
    )
    atomic_write_json(run_dir / "usage.json", report.model_dump(mode="json"))


def summary(status: str = "succeeded", budget: BudgetStatus | None = None) -> RunSummary:
    return RunSummary(status=status, duration_seconds=120, budget_status=budget or BudgetStatus())  # type: ignore[arg-type]


def budget_over() -> BudgetStatus:
    return BudgetStatus(
        state="exceeded",
        exceeded="The run was stopped: max_cost_usd 1.0 exceeded.",
        limits=[BudgetLimitStatus(name="max_cost_usd", used=1.2, max=1.0, fraction=1.2)],
        notes=["max_tokens is not enforced for this model"],
    )


def coverage() -> list[CriterionCoverage]:
    return [
        CriterionCoverage(id="AC-1", text="add stores a note", status="verified", checks=["tests"]),
        CriterionCoverage(id="AC-2", text="list prints notes", note="no mapped check"),
    ]


def finding(**fields: object) -> Finding:
    values: dict[str, object] = {"id": "F-1", "severity": "high", "summary": "SQL built by hand"}
    values.update(fields)
    return Finding.model_validate(values)


PATCH = """diff --git a/app/store.py b/app/store.py
index 111..222 100644
--- a/app/store.py
+++ b/app/store.py
@@ -1,3 +1,4 @@
 import json
-OLD = 1
+NEW = 2
+EXTRA = '<script>alert(1)</script>'
diff --git a/app/new.py b/app/new.py
new file mode 100644
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,2 @@
+a
+b
"""
