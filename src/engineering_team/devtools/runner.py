"""``DevRunner``: every structured developer tool for one run, behind one object.

The agent tools (``tools/dev_tools.py``) call it, and so will the verifier. It builds each
command from a plan, runs it through the run's execution backend (allowlist, command gate,
timeout), reads the tool's own report, and returns a typed report from ``devtools.models``.
"""

from __future__ import annotations

from engineering_team.devtools.runner_base import DEV_EXECUTABLES, Execution
from engineering_team.devtools.runner_deps import DepsMixin
from engineering_team.devtools.runner_static import StaticMixin


class DevRunner(StaticMixin, DepsMixin):
    """Run tests, linters, type checkers, formatters, builds, coverage, installs, and audits."""


__all__ = ["DEV_EXECUTABLES", "DevRunner", "Execution"]
