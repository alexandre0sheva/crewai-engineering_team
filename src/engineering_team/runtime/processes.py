"""Background processes and local ports for one run, with a guarantee that none outlive it.

A dev server, a database, or a watcher is started with :meth:`ProcessRegistry.start`, which goes
through the run's execution backend (so a container backend covers it). The registry owns
every process it starts and kills each one's whole process group when

* its **stage ends** (the stage it was started in; the recorder calls :meth:`stop_stage`),
* the run is **cancelled** (a reaper thread watches the cancel event),
* its **lifetime** limit passes,
* the run **ends or crashes** (the recorder calls :meth:`stop_all` from its ``finally``), or
* the interpreter exits (an ``atexit`` safety net).

One thing cannot be covered from inside the process: if the controller itself is killed with
``SIGKILL`` its children are orphaned. The Docker backend (T20) removes that failure mode.
"""

from __future__ import annotations

import atexit
import logging
import socket
import threading
import time
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime

from engineering_team.contracts import utc_now
from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionBackend,
    ProcessHandle,
)
from engineering_team.runtime.events import EventSink, NullSink, current_stage

log = logging.getLogger(__name__)

REAPER_INTERVAL = 0.25
STOP_GRACE_SECONDS = 3.0
MAX_RESERVED_PORTS = 500
KEPT_STOPPED = 20


class ProcessError(ValueError):
    """A process operation that cannot be done; the message says how to fix the call."""


@dataclass
class ManagedProcess:
    id: str
    name: str
    command: str
    handle: ProcessHandle
    cwd: str
    stage: str | None
    agent: str | None
    started: float  # monotonic, for lifetimes
    started_at: datetime
    max_lifetime: float
    ports: set[int] = field(default_factory=set)
    record: CommandRecord | None = None
    stop_reason: str | None = None
    stopping: bool = False

    @property
    def running(self) -> bool:
        return self.record is None and self.handle.poll() is None

    def age(self, now: float) -> float:
        return now - self.started


_registries: weakref.WeakSet[ProcessRegistry] = weakref.WeakSet()


@atexit.register
def _stop_every_registry() -> None:
    for registry in list(_registries):
        registry.stop_all("the program is exiting")


def free_loopback_port() -> int:
    """A port the operating system says is free right now (it can still be taken afterwards)."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ProcessRegistry:
    """Starts, tracks, and stops one run's background processes; hands out distinct ports."""

    def __init__(
        self,
        backend: ExecutionBackend,
        cancel_event: threading.Event,
        events: EventSink | None = None,
        *,
        max_processes: int = 4,
        max_lifetime: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._cancel = cancel_event
        self._events = events or NullSink()
        self.max_processes = max_processes
        self.max_lifetime = max_lifetime
        self._clock = clock
        self._lock = threading.RLock()
        self._processes: list[ManagedProcess] = []
        self._counter = 0
        self._ports: dict[int, str] = {}  # port -> who reserved or declared it
        self._reaper: threading.Thread | None = None
        _registries.add(self)

    # -- starting and finding ----------------------------------------------------------

    def start(
        self,
        spec: CommandSpec,
        *,
        name: str,
        command: str,
        cwd: str = ".",
        agent: str | None = None,
        ports: Iterable[int] = (),
        max_lifetime: float | None = None,
    ) -> ManagedProcess:
        """Start ``spec`` in the background; refuses a duplicate name or too many processes."""

        with self._lock:
            running = [p for p in self._processes if p.running]
            if len(running) >= self.max_processes:
                names = ", ".join(f"{p.name} ({p.id})" for p in running)
                raise ProcessError(
                    f"{len(running)} background processes are already running (limit "
                    f"{self.max_processes}: {names}). Stop one with Stop Process first."
                )
            if any(p.name == name for p in running):
                raise ProcessError(f"A process named {name!r} is already running; stop it first.")
            wanted = tuple(sorted(set(ports)))
            handle = self._backend.start(replace_ports(spec, wanted))
            self._counter += 1
            process = ManagedProcess(
                id=f"proc-{self._counter}",
                name=name,
                command=command,
                handle=handle,
                cwd=cwd,
                stage=current_stage.get(),
                agent=agent,
                started=self._clock(),
                started_at=utc_now(),
                max_lifetime=float(max_lifetime or self.max_lifetime),
                ports=set(wanted),
            )
            self._processes.append(process)
            for port in wanted:
                self._ports.setdefault(port, name)
            self._trim()
            self._ensure_reaper()
        self._events.emit(
            "process.started",
            process_id=process.id,
            name=name,
            command=command,
            ports=list(wanted),
            agent=agent,
        )
        return process

    def get(self, ref: str) -> ManagedProcess:
        """A process by id (``proc-2``) or name; the newest wins when a name was reused."""

        ref = ref.strip()
        with self._lock:
            matches = [p for p in self._processes if ref in (p.id, p.name)]
            if not matches:
                known = ", ".join(f"{p.name} ({p.id})" for p in self._processes) or "none"
                raise ProcessError(f"No process {ref!r}. Known: {known}. See List Processes.")
            process = matches[-1]
        self._refresh(process)
        return process

    def processes(self) -> list[ManagedProcess]:
        with self._lock:
            current = list(self._processes)
        for process in current:
            self._refresh(process)
        return current

    def running(self) -> list[ManagedProcess]:
        return [p for p in self.processes() if p.running]

    def _refresh(self, process: ManagedProcess) -> None:
        """Record the exit of a process that ended on its own."""

        if process.record is None and not process.stopping and process.handle.poll() is not None:
            with self._lock:
                if process.record is None and not process.stopping:
                    process.record = process.handle.wait()
                    process.stop_reason = process.stop_reason or "exited on its own"
            self._events.emit(
                "process.stopped",
                process_id=process.id,
                name=process.name,
                reason=process.stop_reason,
                exit_code=process.record.exit_code if process.record else None,
            )

    def _trim(self) -> None:
        ended = [p for p in self._processes if not p.running]
        for process in ended[: max(len(ended) - KEPT_STOPPED, 0)]:
            self._processes.remove(process)

    # -- stopping ----------------------------------------------------------------------

    def stop(self, ref: str, reason: str = "stopped") -> ManagedProcess:
        """Kill the process's whole group (SIGTERM, then SIGKILL) and record how it ended."""

        process = self.get(ref)
        self._stop(process, reason)
        return process

    def _stop(self, process: ManagedProcess, reason: str) -> None:
        with self._lock:
            if process.record is not None or process.stopping:
                return
            process.stopping = True
        try:
            record = process.handle.stop(STOP_GRACE_SECONDS)
        except Exception:  # a failing stop must not hide the others
            log.warning("Could not stop %s", process.id, exc_info=True)
            return
        with self._lock:
            process.record = record
            process.stop_reason = reason
        self._events.emit(
            "process.stopped",
            process_id=process.id,
            name=process.name,
            reason=reason,
            exit_code=record.exit_code,
            ran_seconds=round(process.age(self._clock()), 1),
        )

    def stop_all(self, reason: str) -> int:
        """Stop every running process; returns how many were running."""

        with self._lock:
            targets = [p for p in self._processes if p.record is None and not p.stopping]
        for process in targets:
            self._stop(process, reason)
        return len(targets)

    def stop_stage(self, stage: str) -> int:
        """Stop the processes that were started while ``stage`` was the current stage."""

        with self._lock:
            targets = [
                p
                for p in self._processes
                if p.stage == stage and p.record is None and not p.stopping
            ]
        for process in targets:
            self._stop(process, f"stage {stage} ended")
        return len(targets)

    # -- the reaper: cancellation and lifetimes ----------------------------------------

    def _ensure_reaper(self) -> None:
        if self._reaper is None or not self._reaper.is_alive():
            self._reaper = threading.Thread(target=self._reap, name="process-reaper", daemon=True)
            self._reaper.start()

    def _reap(self) -> None:
        while True:
            time.sleep(REAPER_INTERVAL)
            if self._cancel.is_set():
                self.stop_all("the run was cancelled")
            now = self._clock()
            with self._lock:
                expired = [
                    p
                    for p in self._processes
                    if p.record is None and not p.stopping and p.age(now) > p.max_lifetime
                ]
                alive = any(p.record is None and not p.stopping for p in self._processes)
            for process in expired:
                self._stop(process, f"lifetime limit of {process.max_lifetime:.0f}s reached")
            self.processes()  # records processes that exited on their own
            if not alive:
                with self._lock:
                    if not any(p.record is None and not p.stopping for p in self._processes):
                        self._reaper = None
                        return

    # -- ports -------------------------------------------------------------------------

    def reserve_port(self, owner: str = "") -> int:
        """A free loopback port nobody else in this run has been given."""

        with self._lock:
            if len(self._ports) >= MAX_RESERVED_PORTS:
                raise ProcessError(f"{MAX_RESERVED_PORTS} ports are already reserved in this run.")
            for _ in range(200):
                port = free_loopback_port()
                if port not in self._ports:
                    self._ports[port] = owner or "unnamed"
                    return port
        raise ProcessError("Could not find an unreserved free port; try again.")

    def allow_port(self, port: int, owner: str = "declared") -> None:
        """Declare a loopback port as this run's own (the HTTP tool may then call it)."""

        with self._lock:
            self._ports.setdefault(port, owner)

    def is_run_port(self, port: int) -> bool:
        with self._lock:
            return port in self._ports

    def run_ports(self) -> dict[int, str]:
        with self._lock:
            return dict(self._ports)


def replace_ports(spec: CommandSpec, ports: tuple[int, ...]) -> CommandSpec:
    return replace(spec, ports=ports) if ports else spec
