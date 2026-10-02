from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_team.runtime.locks import WorkspaceBusy, WorkspaceLock

HOLDER_SCRIPT = """
import sys, time
from pathlib import Path
from engineering_team.runtime.locks import WorkspaceLock

lock = WorkspaceLock(Path(sys.argv[1])).acquire("child-run")  # keep a reference
print("ready", flush=True)
time.sleep(60)
"""


def _lock_file(root: Path) -> Path:
    return root / ".engineering-team" / "lock"


def test_acquiring_records_the_holder(tmp_path: Path) -> None:
    import os

    lock = WorkspaceLock(tmp_path).acquire("run-1")

    record = json.loads(_lock_file(tmp_path).read_text(encoding="utf-8"))
    assert record == {"pid": os.getpid(), "run_id": "run-1"}
    lock.release()


def test_a_second_holder_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    first = WorkspaceLock(tmp_path).acquire("run-1")

    with pytest.raises(WorkspaceBusy, match=r"run-1.*pid \d+"):
        WorkspaceLock(tmp_path).acquire("run-2")

    first.release()


def test_releasing_lets_another_run_in(tmp_path: Path) -> None:
    WorkspaceLock(tmp_path).acquire("run-1").release()

    second = WorkspaceLock(tmp_path).acquire("run-2")

    assert json.loads(_lock_file(tmp_path).read_text(encoding="utf-8"))["run_id"] == "run-2"
    second.release()


def test_release_is_idempotent_and_a_lock_cannot_be_acquired_twice(tmp_path: Path) -> None:
    lock = WorkspaceLock(tmp_path).acquire("run-1")

    with pytest.raises(RuntimeError, match="already held"):
        lock.acquire("run-1")
    lock.release()
    lock.release()


def test_a_record_left_by_a_dead_process_is_recovered(tmp_path: Path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    dead_pid = process.pid
    _lock_file(tmp_path).parent.mkdir(parents=True)
    _lock_file(tmp_path).write_text(
        json.dumps({"pid": dead_pid, "run_id": "crashed-run"}), encoding="utf-8"
    )

    lock = WorkspaceLock(tmp_path).acquire("run-2")

    assert json.loads(_lock_file(tmp_path).read_text(encoding="utf-8"))["run_id"] == "run-2"
    lock.release()


def test_the_os_drops_the_lock_when_the_holder_is_killed(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(WorkspaceBusy, match="child-run"):
            WorkspaceLock(tmp_path).acquire("run-2")

        child.kill()
        child.wait()

        WorkspaceLock(tmp_path).acquire("run-2").release()
    finally:
        child.kill()
        child.wait()
        if child.stdout:
            child.stdout.close()
