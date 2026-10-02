from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from engineering_team.execution.backend import CommandSpec, ExecutionBackend, ProcessHandle
from engineering_team.execution.local import LocalBackend


def _spec(tmp_path: Path, *argv: str, timeout: float = 30.0) -> CommandSpec:
    return CommandSpec(
        argv=(sys.executable, *argv),
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        timeout=timeout,
        label="test",
    )


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    return LocalBackend(tmp_path / "logs")


def test_the_local_backend_implements_the_protocols(backend: LocalBackend, tmp_path: Path) -> None:
    assert isinstance(backend, ExecutionBackend)
    handle = backend.start(_spec(tmp_path, "-c", "pass"))
    assert isinstance(handle, ProcessHandle)
    handle.wait()


def test_a_command_reports_exit_code_output_and_log(backend: LocalBackend, tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "hello.py",
        "import sys\nprint('to stdout')\nprint('to stderr', file=sys.stderr)\nsys.exit(3)\n",
    )

    record = backend.run(_spec(tmp_path, str(script)))

    assert record.exit_code == 3
    assert not record.timed_out and not record.truncated
    assert "to stdout" in record.output_tail and "to stderr" in record.output_tail
    assert record.log_path == tmp_path / "logs" / "1.log"
    assert "to stdout" in record.log_path.read_text(encoding="utf-8")
    assert record.duration >= 0


def test_commands_get_sequential_ids_and_logs(backend: LocalBackend, tmp_path: Path) -> None:
    first = backend.run(_spec(tmp_path, "-c", "pass"))
    second = backend.run(_spec(tmp_path, "-c", "pass"))

    assert (first.id, second.id) == ("cmd-1", "cmd-2")
    assert second.log_path.name == "2.log"


def test_commands_run_non_interactively(backend: LocalBackend, tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "env.py",
        "import os\nprint(os.getenv('CI'), os.getenv('NO_COLOR'))\n"
        "print(input() if False else 'stdin-closed')\n",
    )

    record = backend.run(_spec(tmp_path, str(script)))

    assert "1 1" in record.output_tail


def test_an_output_flood_is_bounded_in_memory_and_the_log_is_capped(tmp_path: Path) -> None:
    backend = LocalBackend(tmp_path / "logs", max_log_bytes=200_000)
    script = _script(
        tmp_path,
        "flood.py",
        "import sys\nline = 'x' * 99 + '\\n'\n"
        "for _ in range(50_000):\n    sys.stdout.write(line)\n"
        "print('THE END')\n",
    )

    record = backend.run(_spec(tmp_path, str(script)))

    assert record.exit_code == 0 and record.truncated
    assert len(record.output_tail) < 20_000
    assert "THE END" in record.output_tail  # the tail survives
    assert "characters omitted" in record.output_tail
    log = record.log_path.read_bytes()
    assert 200_000 <= len(log) < 201_000
    assert b"log capped" in log


def test_a_timeout_kills_the_command_and_its_grandchild(
    backend: LocalBackend, tmp_path: Path
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = _script(
        tmp_path,
        "spawn.py",
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "print('spawned', flush=True)\n"
        "time.sleep(60)\n",
    )

    started = time.monotonic()
    record = backend.run(_spec(tmp_path, str(script), timeout=1.0))

    assert record.timed_out and record.exit_code != 0
    assert time.monotonic() - started < 15
    assert "spawned" in record.output_tail
    assert _wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(
    backend: LocalBackend, tmp_path: Path
) -> None:
    script = _script(
        tmp_path,
        "stubborn.py",
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\ntime.sleep(60)\n",
    )
    handle = backend.start(_spec(tmp_path, str(script)))
    deadline = time.monotonic() + 10
    while "ready" not in handle.read_output() and time.monotonic() < deadline:
        time.sleep(0.05)

    record = handle.stop(grace=0.5)

    assert record.exit_code < 0
    assert _wait_dead(handle.pid)


def test_cancelling_the_run_kills_the_running_command(tmp_path: Path) -> None:
    cancel = threading.Event()
    backend = LocalBackend(tmp_path / "logs", cancel)
    script = _script(
        tmp_path, "sleepy.py", "import time\nprint('up', flush=True)\ntime.sleep(60)\n"
    )
    threading.Timer(0.5, cancel.set).start()

    started = time.monotonic()
    record = backend.run(_spec(tmp_path, str(script), timeout=60))

    assert record.cancelled and not record.timed_out
    assert time.monotonic() - started < 15


def test_descendants_that_outlive_the_command_are_cleaned_up(
    backend: LocalBackend, tmp_path: Path
) -> None:
    pid_file = tmp_path / "straggler.pid"
    script = _script(
        tmp_path,
        "leave.py",
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n",
    )

    record = backend.run(_spec(tmp_path, str(script)))

    assert record.exit_code == 0
    assert _wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_a_started_process_can_be_polled_and_waited_on(
    backend: LocalBackend, tmp_path: Path
) -> None:
    handle = backend.start(_spec(tmp_path, "-c", "import time; time.sleep(0.3); print('late')"))

    assert handle.poll() is None
    record = handle.wait()

    assert record.exit_code == 0 and "late" in record.output_tail
    assert handle.poll() == 0


def test_a_running_processes_log_file_shows_its_output_before_it_exits(tmp_path: Path) -> None:
    """Regression: the log was a buffered file, so Read File Range / Read Process Logs saw
    nothing until 8 KB had been written or the process ended."""

    script = _script(
        tmp_path,
        "talk.py",
        "import time\nprint('hello from the child', flush=True)\ntime.sleep(30)\n",
    )
    handle = LocalBackend(tmp_path / "logs").start(_spec(tmp_path, str(script)))
    try:
        deadline = time.monotonic() + 10
        while (
            time.monotonic() < deadline
            and "hello from the child" not in handle.log_path.read_text(encoding="utf-8")
        ):
            time.sleep(0.05)

        assert "hello from the child" in handle.log_path.read_text(encoding="utf-8")
        assert handle.poll() is None
    finally:
        handle.stop()
