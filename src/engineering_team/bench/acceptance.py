"""Run a task's hidden acceptance checks against a workspace, outside it.

Each criterion runs in its own process, on its own *copy* of the workspace (so one check cannot
disturb another), from a scratch directory, with a scrubbed environment (no API keys, a throwaway
home). The checks themselves live in the task directory, which no run directory contains.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from engineering_team.bench.tasks import BenchTask, copy_tree

DETAIL_LIMIT = 1500
DEFAULT_TIMEOUT = 120


@dataclass(frozen=True)
class CriterionOutcome:
    id: str
    passed: bool
    required: bool
    detail: str
    duration: float


def source_root() -> str:
    """The directory holding the ``engineering_team`` package this process runs (so a child
    process imports the same code, whether or not the package is installed editable)."""

    return str(Path(__file__).resolve().parents[2])


def clean_environment(home: Path) -> dict[str, str]:
    """The environment a check (and the program it runs) gets: nothing secret, nothing ambient."""

    keep = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TZ")
        if name in os.environ
    }
    return {
        **keep,
        "HOME": str(home),
        "TMPDIR": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": source_root(),
    }


def check_environment(home: Path) -> dict[str, str]:
    """``clean_environment`` plus a start-up shim that keeps name lookups out of the check."""

    env = clean_environment(home)
    shim = str(Path(__file__).resolve().parent / "shim")
    env["PYTHONPATH"] = os.pathsep.join([shim, env["PYTHONPATH"]])
    return env


def _terminate(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_criterion(
    task: BenchTask, criterion_id: str, workspace: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[bool, str, float]:
    """``(passed, detail, seconds)`` for one criterion; passing needs exit status 0."""

    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bench-check-") as scratch:
        scratch_dir = Path(scratch)
        copy = scratch_dir / "workspace"
        copy_tree(workspace, copy)
        home = scratch_dir / "home"
        home.mkdir()
        process = subprocess.Popen(
            [sys.executable, str(task.checks_file), criterion_id, str(copy)],
            cwd=scratch_dir,
            env=check_environment(home),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout)
            passed, detail = process.returncode == 0, output
        except subprocess.TimeoutExpired:
            _terminate(process)
            passed, detail = False, f"FAILED: the check did not finish within {timeout:g}s"
    text = detail.strip()
    if len(text) > DETAIL_LIMIT:
        text = "…" + text[-DETAIL_LIMIT:]
    return passed, text, round(time.monotonic() - began, 2)


def run_acceptance(
    task: BenchTask, workspace: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> list[CriterionOutcome]:
    """Every criterion of ``task`` against ``workspace``, in the task's order."""

    outcomes = []
    for criterion in task.criteria:
        passed, detail, seconds = run_criterion(task, criterion.id, workspace, timeout=timeout)
        outcomes.append(CriterionOutcome(criterion.id, passed, criterion.required, detail, seconds))
    return outcomes


def all_required_passed(outcomes: list[CriterionOutcome]) -> bool:
    return bool(outcomes) and all(o.passed for o in outcomes if o.required)
