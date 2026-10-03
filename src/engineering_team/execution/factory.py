"""Choosing the execution backend for a run from its settings."""

from __future__ import annotations

import threading
from pathlib import Path

from engineering_team.execution.backend import ExecutionBackend, ExecutionUnavailable
from engineering_team.execution.local import LocalBackend
from engineering_team.settings import Settings


def create_backend(
    settings: Settings,
    root: Path,
    log_dir: Path,
    run_id: str,
    cancel_event: threading.Event | None = None,
) -> ExecutionBackend:
    """The backend ``settings.execution.backend`` names. ``docker`` without a working Docker
    is an error (:class:`ExecutionUnavailable`); it never falls back to running on the host."""

    if settings.execution.backend != "docker":
        return LocalBackend(log_dir, cancel_event)
    # Imported here: the local backend must not need Docker's modules (or the settings import).
    from engineering_team.execution.docker import DockerBackend, docker_status

    status = docker_status()
    if not status.ok:
        raise ExecutionUnavailable(f"{status.message} {status.hint or ''}".strip())
    return DockerBackend(root, log_dir, run_id, settings.execution.docker, cancel_event)
