"""A process the caller talks to over stdin and stdout (a language server).

``ExecutionBackend.run`` and ``start`` capture a command's output in a log; a protocol needs the
bytes themselves. A backend that can start such a process offers ``open_stdio(spec)`` (the local
backend and the Docker sandbox do). stderr goes to a log file, never into the stream, and the whole
process group is killed on :meth:`LocalStdioProcess.stop` and when the run is cancelled.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path

from engineering_team.execution.backend import CommandSpec

CHUNK = 65536
KILL_GRACE_SECONDS = 3.0
CANCEL_POLL_SECONDS = 0.25
NON_INTERACTIVE_ENV = {"CI": "1", "NO_COLOR": "1"}


class StdioClosed(Exception):
    """The process ended (or was stopped) while the caller was reading or writing."""


class LocalStdioProcess:
    """A child process with pipes for stdin and stdout, on the host."""

    def __init__(
        self, spec: CommandSpec, log_path: Path, *, cancel_event: threading.Event | None = None
    ) -> None:
        self.spec = spec
        self.log_path = log_path
        self._chunks: queue.Queue[bytes | None] = queue.Queue()
        self._stopped = threading.Event()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("wb")
        try:
            argv, env, cwd = self._launch(spec)
            self._process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log,
                start_new_session=True,
            )
        except BaseException:
            self._log.close()
            raise
        threading.Thread(target=self._pump, name="stdio-reader", daemon=True).start()
        if cancel_event is not None:
            threading.Thread(
                target=self._watch, args=(cancel_event,), name="stdio-cancel", daemon=True
            ).start()

    def _launch(self, spec: CommandSpec) -> tuple[list[str], Mapping[str, str], Path]:
        return list(spec.argv), {**spec.env, **NON_INTERACTIVE_ENV}, spec.cwd

    def _pump(self) -> None:
        stream = self._process.stdout
        assert stream is not None
        try:
            while chunk := os.read(stream.fileno(), CHUNK):
                self._chunks.put(chunk)
        except OSError:
            pass
        finally:
            self._chunks.put(None)  # end of stream

    def _watch(self, cancel_event: threading.Event) -> None:
        while not self._stopped.is_set() and self._process.poll() is None:
            if cancel_event.wait(CANCEL_POLL_SECONDS):
                self.stop(grace=1.0)
                return

    # -- the stream ---------------------------------------------------------------------

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def write(self, data: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None or self._stopped.is_set():
            raise StdioClosed("the process was stopped")
        try:
            stdin.write(data)
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise StdioClosed(f"the process closed its input ({exc})") from exc

    def read(self, timeout: float) -> bytes | None:
        """The next bytes the process wrote; ``b""`` after ``timeout`` with none, ``None`` once
        it has closed its output for good."""

        try:
            chunk = self._chunks.get(timeout=timeout)
        except queue.Empty:
            return b""
        if chunk is None:
            self._chunks.put(None)  # stay at the end for every later read
        return chunk

    # -- lifecycle ----------------------------------------------------------------------

    def stop(self, grace: float = KILL_GRACE_SECONDS) -> None:
        """Close its input, then terminate the process group (SIGTERM, then SIGKILL)."""

        self._stopped.set()
        with contextlib.suppress(OSError, ValueError):
            if self._process.stdin is not None:
                self._process.stdin.close()
        self._signal_group(signal.SIGTERM)
        try:
            self._process.wait(grace)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            self._process.wait()
        with contextlib.suppress(OSError):
            self._log.close()

    def _signal_group(self, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._process.pid, sig)
