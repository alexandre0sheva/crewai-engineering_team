"""A batch: many runs, some in parallel, under one spend cap, resumable."""

from __future__ import annotations

import hashlib
import platform
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from engineering_team.atomic_io import atomic_write_json
from engineering_team.bench.execution import ProcessRegistry, base_record, execute_run
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.results import BATCH_FILE, COUNTED, RunRecord, read_result, write_result
from engineering_team.bench.tasks import BenchTask

OnResult = Callable[[RunRecord], None]


class BudgetLedger:
    """The batch's spend cap. A run *reserves* its own cap before it starts and settles with what
    it actually cost, so parallel runs can never spend more than the total between them."""

    def __init__(self, total: float | None) -> None:
        self.total = total
        self.spent = 0.0
        self._reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float | None) -> bool:
        if self.total is None or amount is None:
            return True
        with self._lock:
            if self.spent + self._reserved + amount > self.total + 1e-9:
                return False
            self._reserved += amount
            return True

    def settle(self, reserved: float | None, actual: float | None) -> None:
        """Release a reservation; an unknown cost counts as the whole reservation."""

        if self.total is None or reserved is None:
            return
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved)
            self.spent += reserved if actual is None else actual


def task_digest(task: BenchTask) -> str:
    """A hash of everything that defines a task, so a result says which version it measured."""

    digest = hashlib.sha256()
    for path in sorted(task.root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(task.root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def _file_digest(path: str | None) -> str | None:
    """A hash of the settings file the batch ran with (``None``: none), for reproducibility."""

    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def write_batch_manifest(
    options: RunOptions,
    tasks: Sequence[BenchTask],
    strategies: Sequence[str],
    repeat: int,
    *,
    planned: int,
    not_applicable: Sequence[tuple[str, str]],
    budget_usd: float | None,
    parallel: int,
) -> None:
    """``batch.json``: what was run and with what, so a report can say how to reproduce it."""

    atomic_write_json(
        options.batch_dir / BATCH_FILE,
        {
            "schema_version": 1,
            "batch": options.batch,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "fake": options.fake,
            "fake_solution": options.fake_solution if options.fake else None,
            "provider": options.provider,
            "profile": options.profile,
            "sandbox": options.sandbox,
            "config_sha256": _file_digest(options.config),
            "strategies": list(strategies),
            "repeat": repeat,
            "parallel": parallel,
            "budget_usd": budget_usd,
            "run_budget_usd": options.run_budget_usd,
            "planned_runs": planned,
            "not_applicable": [{"task": t, "strategy": s} for t, s in not_applicable],
            "tasks": {t.id: {"subset": t.subset, "digest": task_digest(t)} for t in tasks},
            "versions": {
                "engineering_team": _version("engineering_team"),
                "crewai": _version("crewai"),
                "python": platform.python_version(),
                "platform": f"{platform.system()} {platform.machine()}",
            },
            "command": [sys.argv[0], *sys.argv[1:]],
        },
    )


def run_batch(
    specs: Sequence[RunSpec],
    options: RunOptions,
    *,
    parallel: int = 1,
    ledger: BudgetLedger | None = None,
    on_result: OnResult | None = None,
    cancel: threading.Event | None = None,
) -> list[RunRecord]:
    """Run every spec (``parallel`` at a time); returns the records ordered like ``specs``.

    A spec that would overspend the ledger is recorded as ``skipped`` rather than started. With
    ``options.resume`` a run that already has a counted ``result.json`` is kept, not repeated.
    A ``KeyboardInterrupt`` stops every team process, then propagates.
    """

    ledger = ledger or BudgetLedger(None)
    cancel = cancel or threading.Event()
    registry = ProcessRegistry()
    notify = threading.Lock()

    def work(spec: RunSpec) -> RunRecord | None:
        if cancel.is_set():
            return None
        run_dir = options.batch_dir / spec.task.id / f"{spec.strategy}-{spec.repeat}"
        if options.resume and (kept := read_result(run_dir)) and kept.outcome in COUNTED:
            return kept
        cap = options.run_budget_usd
        if not ledger.reserve(cap):
            run_dir.mkdir(parents=True, exist_ok=True)
            skipped = base_record(
                spec,
                options,
                outcome="skipped",
                run_dir=run_dir.relative_to(options.batch_dir).as_posix(),
                reason="the batch budget was spent before this run could start",
            )
            write_result(run_dir, skipped)
            return skipped
        record: RunRecord | None = None
        try:
            record = execute_run(spec, options, registry, cancel)
            return record
        finally:
            ledger.settle(cap, record.cost_usd if record else None)

    futures: dict[Future[RunRecord | None], int] = {}
    records: dict[int, RunRecord] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, parallel), thread_name_prefix="bench")
    try:
        for index, spec in enumerate(specs):
            futures[pool.submit(work, spec)] = index
        for future in as_completed(futures):
            record = future.result()
            if record is None:
                continue
            records[futures[future]] = record
            if on_result is not None:
                with notify:
                    on_result(record)
    except KeyboardInterrupt:
        cancel.set()
        registry.terminate_all()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [records[i] for i in sorted(records)]
