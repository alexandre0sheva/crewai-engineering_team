"""Run commands directly on the host, in their own process group, with bounded memory."""

from __future__ import annotations

import codecs
import contextlib
import itertools
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from engineering_team.execution.backend import CommandRecord, CommandSpec

DEFAULT_MAX_LOG_BYTES = 5_000_000
DEFAULT_HEAD_CHARS = 4_000
DEFAULT_TAIL_CHARS = 12_000
POLL_INTERVAL = 0.05
KILL_GRACE_SECONDS = 3.0
# After the leader exits, children may still hold the output pipe open; give them this long.
PIPE_DRAIN_SECONDS = 1.0

# Extra environment every command gets: no pager/colour noise, tools behave non-interactively.
NON_INTERACTIVE_ENV = {"CI": "1", "NO_COLOR": "1"}


class LocalProcess:
    """One process, its output streaming to a capped log, with head+tail kept in memory."""

    def __init__(
        self,
        spec: CommandSpec,
        command_id: str,
        log_path: Path,
        *,
        cancel_event: threading.Event | None,
        max_log_bytes: int,
        head_chars: int,
        tail_chars: int,
    ) -> None:
        self.id = command_id
        self.log_path = log_path
        self._spec = spec
        self._cancel = cancel_event
        self._max_log_bytes = max_log_bytes
        self._head_limit = head_chars
        self._tail_limit = tail_chars
        self._head = ""
        self._tail = ""
        self._omitted = 0
        self._log_bytes = 0
        self._log_capped = False
        self._lock = threading.Lock()
        self._timed_out = False
        self._cancelled = False
        self._started = time.monotonic()
        self._duration: float | None = None

        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("wb")
        try:
            self._process = subprocess.Popen(
                list(spec.argv),
                cwd=spec.cwd,
                env={**spec.env, **NON_INTERACTIVE_ENV},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # its own process group, so the whole tree can be killed
            )
        except BaseException:
            self._log.close()
            raise
        self._reader = threading.Thread(
            target=self._drain, name=f"{command_id}-output", daemon=True
        )
        self._reader.start()

    # -- output streaming ---------------------------------------------------------------

    def _drain(self) -> None:
        stream = self._process.stdout
        assert stream is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while chunk := os.read(stream.fileno(), 65536):
                self._write_log(chunk)
                self._remember(decoder.decode(chunk))
            self._remember(decoder.decode(b"", final=True))
        except OSError:
            pass
        finally:
            self._log.close()
            stream.close()

    def _write_log(self, chunk: bytes) -> None:
        if self._log_capped:
            return
        room = self._max_log_bytes - self._log_bytes
        if len(chunk) <= room:
            self._log.write(chunk)
            self._log.flush()  # a reader of the log file (a background process) sees it now
            self._log_bytes += len(chunk)
            return
        self._log.write(chunk[: max(room, 0)])
        self._log.write(
            f"\n[log capped at {self._max_log_bytes} bytes; later output discarded]\n".encode()
        )
        self._log.flush()
        self._log_capped = True

    def _remember(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            if len(self._head) < self._head_limit:
                take = self._head_limit - len(self._head)
                self._head += text[:take]
                text = text[take:]
            if not text:
                return
            self._tail += text
            if len(self._tail) > self._tail_limit:
                excess = len(self._tail) - self._tail_limit
                self._omitted += excess
                self._tail = self._tail[excess:]

    def read_output(self) -> str:
        with self._lock:
            if self._omitted:
                return f"{self._head}\n... [{self._omitted} characters omitted] ...\n{self._tail}"
            return self._head + self._tail

    # -- lifecycle ----------------------------------------------------------------------

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> CommandRecord:
        deadline = None if timeout is None else self._started + timeout
        while self._process.poll() is None:
            if self._cancel is not None and self._cancel.is_set():
                self._cancelled = True
                return self.stop()
            if deadline is not None and time.monotonic() >= deadline:
                self._timed_out = True
                return self.stop()
            try:
                self._process.wait(POLL_INTERVAL)
            except subprocess.TimeoutExpired:
                continue
        return self._finish()

    def stop(self, grace: float = KILL_GRACE_SECONDS) -> CommandRecord:
        self._signal_group(signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(grace)
        self._reader.join(grace)
        if self._process.poll() is None or self._reader.is_alive():
            self._signal_group(signal.SIGKILL)
        self._process.wait()
        return self._finish()

    def _signal_group(self, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._process.pid, sig)

    def _finish(self) -> CommandRecord:
        self._reader.join(PIPE_DRAIN_SECONDS)
        if self._reader.is_alive():  # something still holds the pipe: it outlived the command
            self._signal_group(signal.SIGKILL)
            self._reader.join(KILL_GRACE_SECONDS)
        if self._duration is None:
            self._duration = time.monotonic() - self._started
        exit_code = self._process.returncode
        return CommandRecord(
            id=self.id,
            argv=self._spec.argv,
            cwd=self._spec.cwd,
            exit_code=exit_code,
            timed_out=self._timed_out,
            duration=self._duration,
            log_path=self.log_path,
            output_tail=self.read_output(),
            truncated=self._omitted > 0 or self._log_capped,
            cancelled=self._cancelled,
        )


class LocalBackend:
    """Spawn commands on the host; logs go to ``log_dir/<n>.log`` (``run_dir/commands``)."""

    def __init__(
        self,
        log_dir: Path,
        cancel_event: threading.Event | None = None,
        *,
        max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
        head_chars: int = DEFAULT_HEAD_CHARS,
        tail_chars: int = DEFAULT_TAIL_CHARS,
    ) -> None:
        self.log_dir = log_dir
        self._cancel = cancel_event
        self._limits = {
            "max_log_bytes": max_log_bytes,
            "head_chars": head_chars,
            "tail_chars": tail_chars,
        }
        self._numbers = itertools.count(1)
        self._number_lock = threading.Lock()

    def start(self, spec: CommandSpec) -> LocalProcess:
        with self._number_lock:
            number = next(self._numbers)
        return LocalProcess(
            spec,
            f"cmd-{number}",
            self.log_dir / f"{number}.log",
            cancel_event=self._cancel,
            **self._limits,
        )

    def run(self, spec: CommandSpec) -> CommandRecord:
        return self.start(spec).wait(spec.timeout)
