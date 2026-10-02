"""Command execution backends."""

from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionBackend,
    ProcessHandle,
)
from engineering_team.execution.local import LocalBackend

__all__ = ["CommandRecord", "CommandSpec", "ExecutionBackend", "LocalBackend", "ProcessHandle"]
