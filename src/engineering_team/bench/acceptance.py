"""Run a task's hidden acceptance checks against a workspace, outside it.

Each criterion runs in its own process, on its own *copy* of the workspace (so one check cannot
disturb another), from a scratch directory, with a scrubbed environment (no API keys, a throwaway
home). The checks themselves live in the task directory, which no run directory contains.

A criterion is ``passed`` (exit 0), ``failed`` (exit 1, or the check timed out: the team's program
hung), or ``error``: the check could not judge (a missing toolchain, Docker down, a missing check
function). An error is never a fail, so a broken harness cannot make the team look worse.
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
from typing import Literal

from engineering_team.bench.checklib import NODE_MODULES_ENV, SANDBOX_ENV
from engineering_team.bench.repos import CACHE_ENV
from engineering_team.bench.tasks import BenchTask, copy_tree

DETAIL_LIMIT = 1500
DEFAULT_TIMEOUT = 120

Status = Literal["passed", "failed", "error"]
EXIT_STATUS: dict[int, Status] = {0: "passed", 1: "failed"}  # any other exit status: an error


@dataclass(frozen=True)
class CheckSettings:
    """Where toolchain checks run: ``docker`` (the task's image) or ``local`` (this machine)."""

    sandbox: str = "docker"
    node_modules: str | None = None  # local only: a node_modules directory with the task deps
    repo_cache: str | None = None  # local only: the cached checkouts of repository tasks


@dataclass(frozen=True)
class CriterionOutcome:
    id: str
    status: Status
    required: bool
    detail: str
    duration: float

    @property
    def passed(self) -> bool:
        return self.status == "passed"


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


def check_environment(home: Path, settings: CheckSettings | None = None) -> dict[str, str]:
    """``clean_environment`` plus a start-up shim that keeps name lookups out of the check, and
    where its toolchain commands run."""

    settings = settings or CheckSettings()
    env = clean_environment(home)
    shim = str(Path(__file__).resolve().parent / "shim")
    env["PYTHONPATH"] = os.pathsep.join([shim, env["PYTHONPATH"]])
    env[SANDBOX_ENV] = settings.sandbox
    if settings.node_modules:
        env[NODE_MODULES_ENV] = settings.node_modules
    if settings.repo_cache:
        env[CACHE_ENV] = settings.repo_cache
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
    task: BenchTask,
    criterion_id: str,
    workspace: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    settings: CheckSettings | None = None,
) -> tuple[Status, str, float]:
    """``(status, detail, seconds)`` for one criterion: exit 0 passes, 1 fails, else an error."""

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
            env=check_environment(home, settings),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        status: Status
        try:
            output, _ = process.communicate(timeout=timeout)
            status = EXIT_STATUS.get(process.returncode, "error")
            detail = output if status != "error" or output.strip() else (
                f"ERROR: the check ended with status {process.returncode} and no output"
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            _terminate(process)
            status, detail = "failed", f"FAILED: the check did not finish within {timeout:g}s"
    text = detail.strip()
    if len(text) > DETAIL_LIMIT:
        text = "…" + text[-DETAIL_LIMIT:]
    return status, text, round(time.monotonic() - began, 2)


def run_acceptance(
    task: BenchTask,
    workspace: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    settings: CheckSettings | None = None,
) -> list[CriterionOutcome]:
    """Every criterion of ``task`` against ``workspace``, in the task's order."""

    outcomes = []
    for criterion in task.criteria:
        status, detail, seconds = run_criterion(
            task, criterion.id, workspace, timeout=timeout, settings=settings
        )
        outcomes.append(CriterionOutcome(criterion.id, status, criterion.required, detail, seconds))
    return outcomes


def all_required_passed(outcomes: list[CriterionOutcome]) -> bool:
    return bool(outcomes) and all(o.passed for o in outcomes if o.required)


def verdict(outcomes: list[CriterionOutcome]) -> Status:
    """``failed`` when a required criterion failed (a definite answer, whatever else errored),
    else ``error`` when one could not be judged, else ``passed``."""

    required = [o for o in outcomes if o.required]
    if not outcomes or any(o.status == "failed" for o in required):
        return "failed"
    if any(o.status == "error" for o in required):
        return "error"
    return "passed"
