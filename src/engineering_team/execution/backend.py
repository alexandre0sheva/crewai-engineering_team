"""The execution boundary: every project command and process goes through a backend.

Tools never call ``subprocess`` themselves. They describe what to run as a
:class:`CommandSpec` and receive a :class:`CommandRecord`, so a different backend (the Docker
sandbox) can replace :class:`~engineering_team.execution.local.LocalBackend` without touching
any tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandSpec:
    """What to run. ``network=False`` is a request: only sandboxing backends can enforce it."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    label: str = ""
    network: bool = False


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
