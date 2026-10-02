"""One writer per workspace: an OS-level file lock with a human-readable holder record."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import IO

from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

LOCK_FILENAME = "lock"


class WorkspaceBusy(ValueError):
    """Another run holds the workspace. A ``ValueError`` so the CLI reports it as a usage error."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # the process exists but belongs to someone else
        return True
    return True


def _read_record(handle: IO[str]) -> dict[str, object]:
    handle.seek(0)
    try:
        record = json.loads(handle.read() or "{}")
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


class WorkspaceLock:
    """An exclusive ``flock`` on ``<workspace>/.engineering-team/lock``.

    The kernel releases the lock when its holder exits or is killed, so a crashed run never
    leaves the workspace stuck; the leftover record (pid and run id) is simply overwritten
    by the next holder. ``flock`` locks belong to the open file, so two ``WorkspaceLock``
    objects in one process exclude each other too.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.path = workspace_root / CONTROLLER_DIRECTORY / LOCK_FILENAME
        self._handle: IO[str] | None = None

    def acquire(self, run_id: str) -> WorkspaceLock:
        """Take the lock or raise :class:`WorkspaceBusy` naming the current holder."""

        if self._handle is not None:
            raise RuntimeError("The workspace lock is already held by this object.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open without truncating: the record of a live holder must survive a failed attempt.
        handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644), "r+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            record = _read_record(handle)
            handle.close()
            raise WorkspaceBusy(self._busy_message(record)) from None
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "run_id": run_id}))
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        """Release the lock; safe to call more than once."""

        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()  # closing the descriptor drops the flock

    def _busy_message(self, record: dict[str, object]) -> str:
        run_id, pid = record.get("run_id", "unknown"), record.get("pid")
        holder = f"run {run_id}"
        if isinstance(pid, int):
            holder += f" (pid {pid}{'' if _pid_alive(pid) else ', no longer running'})"
        return (
            f"This workspace is in use by {holder}; only one run can write to it at a time. "
            f"Wait for it to finish or stop it (lock: {self.path})."
        )
