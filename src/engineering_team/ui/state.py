"""What the route modules share: the settings, the launcher, and per-run event readers."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException, Request

from engineering_team.runtime.context import RUN_ID_PATTERN
from engineering_team.runtime.run_index import RunRef, locate_run
from engineering_team.runtime.run_store import EVENTS_FILENAME, RunNotFound
from engineering_team.settings import BudgetSettings, Settings
from engineering_team.ui.eventlog import EventLog
from engineering_team.ui.launcher import RunLauncher
from engineering_team.ui.security import Security


@dataclass
class UiState:
    settings: Settings
    launcher: RunLauncher
    security: Security
    demo_repo: str | None = None  # the sample project of ``ui --demo``
    _logs: dict[Path, EventLog] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def log_for(self, ref: RunRef) -> EventLog:
        path = ref.run_dir / EVENTS_FILENAME
        with self._guard:
            if path not in self._logs:
                self._logs[path] = EventLog(path, self.settings.price_table())
            return self._logs[path]

    def budget_for(self, run_id: str) -> BudgetSettings:
        """The budget a run is under: the settings, with what its start changed (a run started
        elsewhere is assumed to use the settings as they are)."""

        record = self.launcher.record(run_id)
        changed = {
            key.removeprefix("budget."): value
            for key, value in (record.overrides if record else {}).items()
            if key.startswith("budget.")
        }
        return self.settings.budget.model_copy(update=changed)

    def find(self, run_id: str) -> RunRef | None:
        """The run with this exact id, or ``None`` (unknown, or not started far enough yet)."""

        if not RUN_ID_PATTERN.fullmatch(run_id):
            return None
        try:
            ref = locate_run(self.settings.workspace_root, run_id)
        except RunNotFound:
            return None
        return ref if ref.run_id == run_id else None

    def need(self, run_id: str) -> RunRef:
        ref = self.find(run_id)
        if ref is None:
            raise HTTPException(404, f"No run {run_id!r}.")
        return ref


def state_of(request: Request) -> UiState:
    ui: UiState = request.app.state.ui
    return ui
