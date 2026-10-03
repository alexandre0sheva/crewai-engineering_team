"""Command execution backends."""

from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionBackend,
    ExecutionError,
    ExecutionUnavailable,
    ProcessHandle,
    close_backend,
    has_executable,
)
from engineering_team.execution.factory import create_backend
from engineering_team.execution.local import LocalBackend

__all__ = [
    "CommandRecord",
    "CommandSpec",
    "ExecutionBackend",
    "ExecutionError",
    "ExecutionUnavailable",
    "LocalBackend",
    "ProcessHandle",
    "close_backend",
    "create_backend",
    "has_executable",
]
