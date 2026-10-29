"""What one benchmark run records (``result.json``) and how a batch is laid out on disk.

``<out>/<batch>/batch.json`` holds the settings of the batch; each run has a directory
``<batch>/<task>/<strategy>-<n>/`` with its ``result.json``, the team's ``stdout.log`` and
``stderr.log``, and the workspace it worked in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from engineering_team.atomic_io import atomic_write_json

# passed: every required criterion holds. failed: the team finished (or stopped) and one does not.
# timeout: it ran out of wall-clock time. error: the harness could not run it, or a check could
# not judge it (not the team's fault; left out of pass rates and never kept by ``--resume``).
# skipped: not started, because the batch budget was spent.
Outcome = Literal["passed", "failed", "timeout", "error", "skipped"]
# api: per-token money. subscription: covered by a plan (cost unknown, never $0). unknown: could be
# either (charged like api). none: an offline run.
RunBilling = Literal["api", "subscription", "unknown", "none"]
COUNTED: frozenset[str] = frozenset({"passed", "failed", "timeout"})

RESULT_FILE = "result.json"
BATCH_FILE = "batch.json"
SCHEMA_VERSION = 3  # 3: ``agent``, ``agent_version``, ``billing``; 2: ``split``, ``intent``


class CriterionRecord(BaseModel):
    id: str
    passed: bool
    error: bool = False  # the check could not judge (a missing toolchain): neither pass nor fail
    required: bool = True
    detail: str = ""
    duration: float = 0.0


class RunRecord(BaseModel):
    """One run: what was asked, how it ended, what it cost, and which criteria held."""

    schema_version: int = SCHEMA_VERSION
    batch: str
    task: str
    strategy: str  # the team's strategy, or the name of the external agent that did the run
    repeat: int
    kind: str
    mode: str
    split: str = Field(validation_alias=AliasChoices("split", "subset"))
    intent: str = ""
    agent: str | None = None  # set for an external agent (claude-code, codex, ...)
    agent_version: str | None = None
    provider: str | None = None
    profile: str | None = None
    fake: bool = False
    outcome: Outcome
    reason: str = ""  # why it did not pass, in a sentence (empty for a pass)
    run_id: str = ""
    exit_code: int | None = None
    team_status: str | None = None
    team_verdict: str | None = None
    duration_seconds: float = 0.0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    sessions: int = 0  # agent sessions (``agent.started`` events)
    cached_prompt_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float | None = None  # None: unknown (an unpriced model, or a plan), never $0
    billing: RunBilling = "api"
    equivalent_cost_usd: float | None = None  # a plan run: the API-equivalent cost it reported
    models: list[str] = []
    repair_rounds: int = 0
    tool_failures: int = 0
    setup_failures: int = 0
    criteria: list[CriterionRecord] = []
    started: str = ""
    finished: str = ""
    run_dir: str = ""  # relative to the batch directory
    workspace: str = ""  # relative to the batch directory

    @property
    def contender(self) -> str:
        """Who did the run: the external agent, or the team's strategy."""

        return self.agent or self.strategy

    @property
    def counted(self) -> bool:
        return self.outcome in COUNTED

    @property
    def failed_criteria(self) -> list[str]:
        return [c.id for c in self.criteria if c.required and not c.passed and not c.error]

    @property
    def errored_criteria(self) -> list[str]:
        return [c.id for c in self.criteria if c.required and c.error]


def write_result(run_dir: Path, record: RunRecord) -> None:
    atomic_write_json(run_dir / RESULT_FILE, record.model_dump(mode="json"))


def read_result(run_dir: Path) -> RunRecord | None:
    try:
        return RunRecord.model_validate_json((run_dir / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def read_batch(batch_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((batch_dir / BATCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def collect_results(batch_dir: Path) -> list[RunRecord]:
    """Every readable ``result.json`` of a batch, in a stable order (task, strategy, repeat)."""

    found = [
        record
        for path in sorted(batch_dir.glob(f"*/*/{RESULT_FILE}"))
        if (record := read_result(path.parent)) is not None
    ]
    return sorted(found, key=lambda r: (r.task, r.strategy, r.repeat))


def batch_directories(out_dir: Path) -> Iterator[Path]:
    """The batches under ``out_dir``, oldest first (by when their manifest was last written)."""

    if out_dir.is_dir():
        found = [p for p in out_dir.iterdir() if (p / BATCH_FILE).is_file()]
        yield from sorted(found, key=lambda p: ((p / BATCH_FILE).stat().st_mtime_ns, p.name))
