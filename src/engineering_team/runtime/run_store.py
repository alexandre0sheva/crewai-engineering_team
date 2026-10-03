"""The run directory: manifests with validated status transitions, written atomically.

Layout of ``<workspace>/.engineering-team/runs/<run_id>/`` (see docs/ARCHITECTURE.md):
``manifest.json`` (this module), ``events.jsonl`` (``runtime.events``), ``request.md``,
``settings.json``, ``crew-log.json``, and ``commands/`` (command logs).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import RunManifest, RunStatus, StageRecord, utc_now
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

MANIFEST_FILENAME = "manifest.json"
EVENTS_FILENAME = "events.jsonl"

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {"succeeded", "failed", "cancelled", "interrupted"}
)
# ``succeeded`` is final; the other end states can be left only by resuming the run.
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "pending": frozenset({"running", "cancelled", "failed"}),
    "running": TERMINAL_STATUSES,
    "interrupted": frozenset({"running"}),
    "failed": frozenset({"running"}),
    "cancelled": frozenset({"running"}),
}


class RunNotFound(ValueError):
    """No run with that id exists in the workspace."""


class InvalidTransition(ValueError):
    """A run status change the lifecycle does not allow."""


_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(runs_dir: Path) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(runs_dir, threading.RLock())


class RunStore:
    """Reads and writes the runs of one workspace. Instances for the same workspace share a lock."""

    def __init__(self, workspace_root: Path) -> None:
        self.runs_dir = workspace_root / CONTROLLER_DIRECTORY / "runs"
        self._lock = _lock_for(self.runs_dir)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / MANIFEST_FILENAME

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / EVENTS_FILENAME

    def create(self, manifest: RunManifest) -> RunManifest:
        with self._lock:
            if self.manifest_path(manifest.run_id).exists():
                raise ValueError(f"Run {manifest.run_id} already exists.")
            self._write(manifest)
        return manifest

    def load(self, run_id: str) -> RunManifest:
        try:
            return RunManifest.model_validate_json(
                self.manifest_path(run_id).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            raise RunNotFound(f"No run {run_id!r} in {self.runs_dir}.") from None

    def list_runs(self) -> list[RunManifest]:
        """Every readable run, oldest first (run ids sort by creation time)."""

        manifests: list[RunManifest] = []
        if not self.runs_dir.is_dir():
            return manifests
        for entry in sorted(self.runs_dir.iterdir()):
            try:
                manifests.append(self.load(entry.name))
            except (RunNotFound, ValidationError, ValueError, OSError):
                continue  # a directory without a valid manifest is not a run
        return manifests

    def latest(self) -> RunManifest | None:
        runs = self.list_runs()
        return runs[-1] if runs else None

    def update(self, run_id: str, change: Callable[[RunManifest], None]) -> RunManifest:
        """Load, apply ``change``, validate any status transition, and write back atomically."""

        with self._lock:
            manifest = self.load(run_id)
            before = manifest.status
            change(manifest)
            if manifest.status != before:
                if manifest.status not in TRANSITIONS.get(before, frozenset()):
                    raise InvalidTransition(
                        f"Run {run_id} cannot go from {before} to {manifest.status}."
                    )
                if manifest.status in TERMINAL_STATUSES and manifest.finished is None:
                    manifest.finished = utc_now()
                if manifest.status == "running":  # a resumed run is open again
                    manifest.finished = None
                    manifest.summary = None
            self._write(manifest)
            return manifest

    def set_status(self, run_id: str, status: RunStatus) -> RunManifest:
        def change(manifest: RunManifest) -> None:
            if manifest.status == status:
                raise InvalidTransition(f"Run {run_id} is already {status}.")
            manifest.status = status

        return self.update(run_id, change)

    def record_stage(self, run_id: str, stage: StageRecord) -> RunManifest:
        """Insert or replace the record for ``stage.name``, keeping first-seen order."""

        def change(manifest: RunManifest) -> None:
            for index, existing in enumerate(manifest.stages):
                if existing.name == stage.name:
                    manifest.stages[index] = stage
                    return
            manifest.stages.append(stage)

        return self.update(run_id, change)

    def _write(self, manifest: RunManifest) -> None:
        atomic_write_json(self.manifest_path(manifest.run_id), manifest.model_dump(mode="json"))


def list_runs(workspace_root: Path) -> list[RunManifest]:
    return RunStore(workspace_root).list_runs()
