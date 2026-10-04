"""Run one benchmark run: start the team as a subprocess, then judge what it left.

The team runs in a process (and process group) of its own, so a crash, a hang, or a runaway cost
cannot take the harness with it and a timeout can kill the whole tree. The verdict comes from the
hidden acceptance checks, never from what the team says about itself.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engineering_team.bench.acceptance import all_required_passed, run_acceptance
from engineering_team.bench.metrics import event_metrics, parse_summary
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.prepare import Prepared, prepare
from engineering_team.bench.results import CriterionRecord, RunRecord, write_result
from engineering_team.bench.tasks import BenchError

GRACE_SECONDS = 10


@dataclass
class ProcessRegistry:
    """The team processes in flight, so an interrupted batch can stop them all."""

    _active: set[subprocess.Popen[bytes]] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._active.add(process)

    def remove(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._active.discard(process)

    def terminate_all(self) -> None:
        with self._lock:
            active = list(self._active)
        for process in active:
            stop(process)


def stop(process: subprocess.Popen[bytes]) -> None:
    """End a team process and everything it started: ask politely, then insist."""

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _inside(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def locate_workspace(summary: dict[str, Any], prepared: Prepared) -> Path:
    """Where the team's work ended up: what it reported, if that is inside the run directory."""

    reported = summary.get("workspace")
    if isinstance(reported, str) and _inside(Path(reported), prepared.run_dir):
        return Path(reported)
    return prepared.workspace


def wait_for(
    process: subprocess.Popen[bytes], timeout: float, cancel: threading.Event
) -> tuple[bool, bool]:
    """Wait for the process: ``(timed_out, interrupted)``. A cancelled batch stops it."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            process.wait(timeout=0.5)
            return False, False
        except subprocess.TimeoutExpired:
            pass
        if cancel.is_set():
            stop(process)
            return False, True
        if time.monotonic() > deadline:
            stop(process)
            return True, False


def base_record(spec: RunSpec, options: RunOptions, **fields: Any) -> RunRecord:
    task = spec.task
    return RunRecord(
        batch=options.batch,
        task=task.id,
        strategy=spec.strategy,
        repeat=spec.repeat,
        kind=task.kind,
        mode=task.mode,
        subset=task.subset,
        provider=options.provider,
        profile=options.profile,
        fake=options.fake,
        outcome=fields.pop("outcome", "error"),
        **fields,
    )


def execute_run(
    spec: RunSpec, options: RunOptions, registry: ProcessRegistry, cancel: threading.Event
) -> RunRecord:
    """Make one run and judge it; always returns a record (and writes its ``result.json``)."""

    run_dir = options.batch_dir / spec.task.id / f"{spec.strategy}-{spec.repeat}"
    relative = run_dir.relative_to(options.batch_dir).as_posix()
    started = _now()
    if run_dir.exists():
        shutil.rmtree(run_dir)  # a rerun of an unfinished or failed-to-run directory
    try:
        prepared = prepare(spec, options, run_dir)
    except (OSError, BenchError, subprocess.CalledProcessError) as exc:
        record = base_record(
            spec, options, outcome="error", reason=f"setup failed: {exc}", started=started,
            finished=_now(), run_dir=relative,
        )  # fmt: skip
        run_dir.mkdir(parents=True, exist_ok=True)
        write_result(run_dir, record)
        return record

    stdout, stderr = run_dir / "stdout.log", run_dir / "stderr.log"
    began = time.monotonic()
    timed_out = interrupted = False
    exit_code: int | None = None
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            process = subprocess.Popen(
                prepared.argv, cwd=run_dir, env=prepared.env, stdout=out, stderr=err,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )  # fmt: skip
        except OSError as exc:
            record = base_record(
                spec, options, outcome="error", reason=f"could not start the team: {exc}",
                started=started, finished=_now(), run_dir=relative,
            )  # fmt: skip
            write_result(run_dir, record)
            return record
        registry.add(process)
        try:
            timed_out, interrupted = wait_for(process, spec.task.timeout_seconds, cancel)
            exit_code = process.returncode
        finally:
            registry.remove(process)
    duration = round(time.monotonic() - began, 2)

    summary = parse_summary(stdout)
    workspace = locate_workspace(summary, prepared)
    metrics = event_metrics(
        workspace / ".engineering-team" / "runs" / prepared.run_id / "events.jsonl"
    )
    reported = summary.get("usage")
    usage: dict[str, Any] = reported if isinstance(reported, dict) else metrics.usage
    outcomes = run_acceptance(spec.task, workspace, timeout=options.acceptance_timeout)
    criteria = [
        CriterionRecord(id=o.id, passed=o.passed, required=o.required, detail=o.detail,
                        duration=o.duration)
        for o in outcomes
    ]  # fmt: skip

    reason, outcome = "", "passed"
    if interrupted:
        outcome, reason = "error", "the batch was interrupted"
    elif timed_out:
        outcome, reason = "timeout", f"exceeded the {spec.task.timeout_seconds}s time limit"
    elif not all_required_passed(outcomes):
        failing = ", ".join(c.id for c in criteria if c.required and not c.passed)
        outcome, reason = "failed", f"criteria not met: {failing}"
        if exit_code not in (0, None):
            team = summary.get("error") or f"exit code {exit_code}"
            reason += f" (the team ended: {str(team)[:200]})"
    record = base_record(
        spec, options, outcome=outcome, reason=reason, run_id=prepared.run_id,
        exit_code=exit_code, team_status=summary.get("status"), team_verdict=summary.get("verdict"),
        duration_seconds=duration, tokens=int(usage.get("tokens") or 0),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        model_calls=int(usage.get("model_calls") or 0),
        tool_calls=int(usage.get("tool_calls") or 0),
        cost_usd=usage.get("estimated_cost_usd") if usage else None, models=metrics.models,
        repair_rounds=metrics.repair_rounds, tool_failures=metrics.tool_failures,
        setup_failures=metrics.setup_failures, criteria=criteria, started=started,
        finished=_now(), run_dir=relative,
        workspace=_relative(workspace, options.batch_dir),
    )  # fmt: skip
    write_result(run_dir, record)
    return record


def _relative(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)
