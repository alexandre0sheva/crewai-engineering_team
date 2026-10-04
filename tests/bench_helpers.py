"""Shared pieces for the benchmark tests: records, a stub team, and the shipped suite."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from engineering_team.bench.results import CriterionRecord, RunRecord

SUITE = Path(__file__).resolve().parents[1] / "benchmarks"


def record(task: str = "notes-cli", strategy: str = "pipeline", repeat: int = 1, **fields: object):
    """A run record; defaults describe a passed run."""

    values: dict[str, object] = {
        "batch": "b", "task": task, "strategy": strategy, "repeat": repeat, "kind": "greenfield",
        "mode": "new", "subset": "dev", "outcome": "passed", "cost_usd": 0.5,
        "duration_seconds": 60.0,
    }  # fmt: skip
    values.update(fields)
    return RunRecord.model_validate(values)


def failing(*criteria: str) -> list[CriterionRecord]:
    return [CriterionRecord(id=c, passed=False) for c in criteria]


STUB_TEAM = '''
"""Stands in for ``python -m engineering_team``: writes the task's reference solution, some
events, and a JSON summary, as a real run would. Behaviour is chosen by the first request line."""
import json, os, shutil, sys, time
from pathlib import Path

argv = sys.argv[1:]
def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

root, run_id = Path(value("--workspace-root")), value("--run-id")
request = Path(value("--request-file") or "/dev/null")
mode = os.environ.get("STUB_MODE", "pass")
if mode == "hang":
    time.sleep(60)
if mode == "crash":
    sys.exit("the stub crashed")
workspace = root / "app"
if mode != "empty":
    shutil.copytree(Path(os.environ["STUB_REFERENCE"]), workspace, dirs_exist_ok=True)
events = workspace / ".engineering-team" / "runs" / run_id
events.mkdir(parents=True, exist_ok=True)
lines = [
    {"seq": 1, "ts": "2026-01-01T00:00:00Z", "run_id": run_id, "type": "check.started",
     "data": {"check": "setup", "kind": "setup"}},
    {"seq": 2, "ts": "2026-01-01T00:00:01Z", "run_id": run_id, "type": "check.finished",
     "data": {"check": "setup", "status": "failed"}},
    {"seq": 3, "ts": "2026-01-01T00:00:02Z", "run_id": run_id, "type": "verify.repair",
     "data": {"round": 1}},
    {"seq": 4, "ts": "2026-01-01T00:00:03Z", "run_id": run_id, "type": "tool.call",
     "data": {"tool": "Run", "ok": False}},
    {"seq": 5, "ts": "2026-01-01T00:00:04Z", "run_id": run_id, "type": "tool.call",
     "data": {"tool": "Run", "ok": True}},
    {"seq": 6, "ts": "2026-01-01T00:00:05Z", "run_id": run_id, "type": "llm.call",
     "agent": "dev", "data": {"model": "openai/gpt-6.1-sol",
                              "usage": {"prompt_tokens": 1000000, "completion_tokens": 100000}}},
]
(events / "events.jsonl").write_text("\\n".join(json.dumps(l) for l in lines) + "\\n")
summary = {"run_id": run_id, "status": "succeeded", "verdict": "verified", "exit_code": 0,
           "workspace": str(workspace)}
if mode == "summary-usage":
    summary["usage"] = {"tokens": 7, "prompt_tokens": 5, "completion_tokens": 2, "model_calls": 1,
                        "tool_calls": 1, "estimated_cost_usd": None}
print("noise {}  before the summary")
print(json.dumps(summary, indent=2))
'''


def stub_python(directory: Path) -> str:
    """An executable that behaves like ``python -m engineering_team ...`` (see ``STUB_TEAM``)."""

    script = directory / "stub_team.py"
    script.write_text(STUB_TEAM)
    wrapper = directory / "stub-python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return str(wrapper)
