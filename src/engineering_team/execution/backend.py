"""The execution boundary: every project command and process goes through a backend.

Tools never call ``subprocess`` themselves. They describe what to run as a
:class:`CommandSpec` and receive a :class:`CommandRecord`, so a different backend (the Docker
sandbox) can replace :class:`~engineering_team.execution.local.LocalBackend` without touching
any tool.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class ExecutionUnavailable(ValueError):
    """The configured backend cannot be used (Docker missing, say). A usage error: the run
    does not start, and it never falls back to another backend silently."""


class ExecutionError(OSError):
    """A command could not be started by the backend (tools report it as an ``ERROR:``)."""


@dataclass(frozen=True)
class CommandSpec:
    """What to run. ``network=False`` is a request: only sandboxing backends can enforce it.

    ``ports`` is the extension point for container backends: the TCP ports the process will
    listen on. A sandboxing backend must publish each one only as ``127.0.0.1:<port>`` (never on
    all interfaces) so the host-side HTTP and browser tools can reach it and nothing else can.
    The local backend runs on the host's loopback already and ignores it.
    """

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    label: str = ""
    network: bool = False
    ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class CommandRecord:
    """The outcome of one command.

    ``output_tail`` is a bounded head-and-tail window of the combined stdout/stderr; the whole
    stream (up to the backend's log cap) is in ``log_path``. ``exit_code`` is negative when a
    signal ended the process, which is how timeouts and cancellations show up.
    """

    id: str
    argv: tuple[str, ...]
    cwd: Path
    exit_code: int
    timed_out: bool
    duration: float
    log_path: Path
    output_tail: str
    truncated: bool
    cancelled: bool = False


@runtime_checkable
class ProcessHandle(Protocol):
    """A running process whose output is streaming to a log (used for background processes)."""

    id: str
    log_path: Path

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """The exit code, or ``None`` while the process runs."""

    def read_output(self) -> str:
        """The current head-and-tail window of the output."""

    def wait(self, timeout: float | None = None) -> CommandRecord:
        """Wait for exit (killing the process group on timeout) and describe the outcome."""

    def stop(self, grace: float = 3.0) -> CommandRecord:
        """Terminate the whole process group (SIGTERM, then SIGKILL) and describe the outcome."""


@runtime_checkable
class ExecutionBackend(Protocol):
    """Runs commands for a run. Implementations must be safe to call from several threads."""

    def run(self, spec: CommandSpec) -> CommandRecord:
        """Run to completion, enforcing ``spec.timeout`` and the run's cancellation."""

    def start(self, spec: CommandSpec) -> ProcessHandle:
        """Start a long-lived process and return immediately."""


def has_executable(backend: object, name: str) -> bool:
    """Whether ``name`` can be run by ``backend``: on the host's ``PATH`` for a backend that
    runs there, and assumed for one that resolves programs itself (a missing one fails when
    it runs, with the usual "not found" output)."""

    if getattr(backend, "resolves_on_host", True):
        return shutil.which(name) is not None
    return True


def close_backend(backend: object, reason: str) -> None:
    """Release what a backend still holds (containers) at the end of a run, if it holds any."""

    close = getattr(backend, "close", None)
    if callable(close):
        close(reason)
