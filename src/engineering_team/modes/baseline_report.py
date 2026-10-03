"""The baseline's data: ``BaselineReport`` and the stable keys of known failures.

Kept apart from the runner (``modes/baseline.py``) so the pipeline state can hold a report
without importing the verifier. ``known_failures`` holds stable keys (``test:<dir>:<test id>``,
``lint:<dir>:<file>:<rule>``, ``build:<dir>``); line numbers are left out of diagnostics so an
edit elsewhere in a file does not turn a known failure into a new one.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import CheckKind, CheckResult, CheckStatus, Contract, utc_now
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

BASELINE_FILE = Path(CONTROLLER_DIRECTORY) / "baseline.json"
MAX_KEYS = 500


class BaselineCheck(Contract):
    """One check of the baseline and how it ended (``unavailable``: it could not run)."""

    id: str
    name: str
    kind: CheckKind
    directory: str = "."
    status: CheckStatus
    summary: str = ""
    command: str = ""
    exit_code: int | None = None
    duration: float = 0.0
    log_path: str | None = None
    hint: str | None = None
    failing: list[str] = Field(default_factory=list)  # this check's known-failure keys


class BaselineReport(Contract):
    revision: str = ""  # the tree the baseline was taken on
    created: datetime = Field(default_factory=utc_now)
    checks: list[BaselineCheck] = Field(default_factory=list)
    known_failures: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def green(self) -> bool:
        """Every check that could run passed (a project with no checks is not green)."""

        ran = [c for c in self.checks if c.status != "unavailable"]
        return bool(ran) and all(c.status == "passed" for c in ran)

    def lines(self) -> list[str]:
        rows = [f"{c.status:<11} {c.name}: {c.summary}".rstrip(": ") for c in self.checks]
        return rows or ["No checks could be run."]


def save_baseline(workspace_root: Path, report: BaselineReport) -> Path:
    path = workspace_root / BASELINE_FILE
    atomic_write_json(path, report.model_dump(mode="json"))
    return path


def load_baseline(workspace_root: Path) -> BaselineReport | None:
    try:
        return BaselineReport.model_validate_json(
            (workspace_root / BASELINE_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


# -- from a check result to stable keys -----------------------------------------------------


def failure_keys(result: CheckResult, directory: str) -> list[str]:
    """The stable keys of what failed in ``result`` (the check's own key if nothing finer is
    known), at most ``MAX_KEYS``."""

    report: dict[str, Any] = result.report or {}
    keys: list[str] = []
    if result.kind == "test":
        keys = [
            f"test:{directory}:{item['test_id']}"
            for item in report.get("failures", [])
            if isinstance(item, dict) and item.get("test_id")
        ]
    elif result.kind in ("lint", "typecheck"):
        for item in report.get("diagnostics", []):
            if isinstance(item, dict) and item.get("severity", "error") == "error":
                what = item.get("rule") or str(item.get("message", ""))[:40]
                keys.append(f"{result.kind}:{directory}:{item.get('file') or '-'}:{what}")
    if not keys:
        keys = [f"{result.kind}:{directory}"]
    return sorted(dict.fromkeys(keys))[:MAX_KEYS]
