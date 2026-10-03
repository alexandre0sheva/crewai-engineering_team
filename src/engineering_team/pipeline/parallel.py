"""The parallel execution engine: work packages in lanes, and read-only fan-out.

**Parallelism is by ownership, not by merging.** The plan gives every work package the path
globs it owns; its agent gets write tools scoped to them, so concurrent agents cannot touch
each other's files (reads stay unrestricted), and shared project files belong to the foundation
and integrate stages. :func:`run_work_packages` runs the plan's topological layers in turn;
inside a layer, packages with disjoint ownership run together (at most
``parallel.max_parallel_agents`` at a time) and packages that might overlap are run one after
the other with a warning. A failed package never stops its siblings; its dependents are
skipped and the caller decides what a missing package means.

Each concurrent unit runs in a numbered **lane** (the lowest free number): events and board
cards carry it so a front end can draw parallel lanes, and a lane's browser session and
processes are its own. Workers run under ``contextvars.copy_context()`` so the run, stage, and
lane tags reach everything they do.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, TypeVar

from crewai.tools import BaseTool

from engineering_team.contracts import Plan, WorkPackage
from engineering_team.pipeline.packages import layers, schedule
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import lane_scope
from engineering_team.tools import WriteScope, build_tools

MAX_ERROR = 600
T = TypeVar("T")
R = TypeVar("R")

# Runs one work package on one lane; raises to fail the attempt. The string is the retry note
# (empty on the first attempt, the previous error afterwards). Returns the agent's summary.
PackageJob = Callable[[WorkPackage, int, str], str]


@dataclass(frozen=True)
class PackageOutcome:
    """How one work package ended. ``skipped``: a package it depends on did not succeed;
    ``reused``: it had already succeeded in an earlier session and was not run."""

    id: str
    status: Literal["succeeded", "failed", "skipped", "reused"]
    lane: int | None = None
    attempts: int = 0
    summary: str = ""
    error: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("succeeded", "reused")


class _Lanes:
    """Hands out the lowest free lane number (1-based)."""

    def __init__(self) -> None:
        self._busy: set[int] = set()
        self._lock = threading.Lock()

    def take(self) -> int:
        with self._lock:
            lane = next(n for n in range(1, len(self._busy) + 2) if n not in self._busy)
            self._busy.add(lane)
            return lane

    def give(self, lane: int) -> None:
        with self._lock:
            self._busy.discard(lane)


def _fan_out(
    ctx: RunContext,
    items: Sequence[T],
    work: Callable[[T, int], R],
    max_parallel: int,
) -> list[R | Exception]:
    """Run ``work(item, lane)`` for every item, ``max_parallel`` at a time, in lanes.

    Results come back in the order of ``items``, whatever order they finished in; an ordinary
    exception is returned in place of its result so one failure never stops the others.
    :class:`RunCancelled` and :class:`BudgetExceeded` stop the run: they are raised once every
    running unit has finished (the cancel event they share makes the rest wrap up quickly).
    """

    if not items:
        return []
    lanes = _Lanes()

    def in_lane(item: T) -> R:
        lane = lanes.take()
        try:
            with lane_scope(lane):
                return work(item, lane)
        finally:
            lanes.give(lane)

    results: list[R | Exception] = []
    fatal: BaseException | None = None
    with ThreadPoolExecutor(
        max_workers=max(1, min(max_parallel, len(items))), thread_name_prefix="lane"
    ) as pool:
        futures = [pool.submit(contextvars.copy_context().run, in_lane, item) for item in items]
        for future in futures:
            try:
                results.append(future.result())
            except (RunCancelled, BudgetExceeded) as exc:
                fatal = fatal or exc
                results.append(exc)
            except Exception as exc:
                results.append(exc)
    if fatal is not None:
        raise fatal
    return results


# -- work packages ---------------------------------------------------------------------------


def run_work_packages(
    ctx: RunContext,
    plan: Plan,
    job: PackageJob,
    max_parallel: int,
    *,
    done: Collection[str] = (),
    retry: int = 1,
) -> list[PackageOutcome]:
    """Run the plan's work packages and return one outcome per package, in plan order.

    ``done`` are packages that already succeeded (not run again). Each failing package is
    retried ``retry`` times with its previous error as the note, then recorded ``failed``; its
    dependents are ``skipped``; independent packages carry on. With ``max_parallel`` 1 nothing
    runs concurrently and packages run in dependency order, exactly as a sequential run would.
    """

    by_id = {package.id: package for package in plan.work_packages}
    outcomes: dict[str, PackageOutcome] = {
        pid: PackageOutcome(pid, "reused") for pid in by_id if pid in done
    }
    unsuccessful: set[str] = set()
    parallel = max_parallel > 1
    plan_layers = layers(plan.work_packages)
    ctx.events.emit(
        "parallel.plan",
        max_parallel=max_parallel,
        layers=[[p.id for p in layer] for layer in plan_layers],
    )
    for layer in plan_layers:
        pending = [package for package in layer if package.id not in outcomes]
        batches = schedule(pending) if parallel else [pending]
        if len(batches) > 1:
            ctx.events.emit(
                "parallel.serialised",
                packages=[[p.id for p in batch] for batch in batches],
                warning="work packages in one layer own overlapping paths, so they run in turn",
            )
        for batch in batches:
            runnable: list[WorkPackage] = []
            for package in batch:
                blocked = [d for d in package.depends_on if d in unsuccessful]
                if blocked:
                    outcomes[package.id] = PackageOutcome(
                        package.id,
                        "skipped",
                        error=f"depends on {', '.join(blocked)}, which did not succeed",
                    )
                    unsuccessful.add(package.id)
                else:
                    runnable.append(package)
            check_cancelled(ctx)

            def work(package: WorkPackage, lane: int) -> PackageOutcome:
                return _attempts(ctx, package, lane, job, retry)

            for package, result in zip(
                runnable, _fan_out(ctx, runnable, work, max_parallel), strict=True
            ):
                if isinstance(result, Exception):  # a bug in the job runner itself
                    result = PackageOutcome(package.id, "failed", error=_clip(result))
                outcomes[package.id] = result
                if not result.ok:
                    unsuccessful.add(package.id)
    return [outcomes[package.id] for package in plan.work_packages]


def _clip(error: object) -> str:
    return (
        f"{type(error).__name__}: {error}"[:MAX_ERROR]
        if isinstance(error, BaseException)
        else str(error)[:MAX_ERROR]
    )


def _attempts(
    ctx: RunContext, package: WorkPackage, lane: int, job: PackageJob, retry: int
) -> PackageOutcome:
    started = time.monotonic()
    ctx.events.emit("lane.started", lane=lane, kind="package", unit=package.id)
    note, error, attempts = "", "", 0
    for attempt in range(1 + max(0, retry)):
        check_cancelled(ctx)
        attempts = attempt + 1
        try:
            summary = job(package, lane, note)
        except (RunCancelled, BudgetExceeded):
            ctx.events.emit("lane.finished", lane=lane, unit=package.id, status="cancelled")
            raise
        except Exception as exc:
            error = str(exc)[:MAX_ERROR]
            note = f"The previous attempt failed: {error}"
            if attempt < retry:
                ctx.events.emit(
                    "lane.retry", lane=lane, unit=package.id, attempt=attempts, error=error
                )
            continue
        seconds = round(time.monotonic() - started, 3)
        ctx.events.emit("lane.finished", lane=lane, unit=package.id, status="succeeded")
        return PackageOutcome(package.id, "succeeded", lane, attempts, summary, "", seconds)
    seconds = round(time.monotonic() - started, 3)
    ctx.events.emit("lane.finished", lane=lane, unit=package.id, status="failed", error=error)
    return PackageOutcome(package.id, "failed", lane, attempts, "", error, seconds)


# -- read-only fan-out -----------------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyJob:
    """One reviewer or analyst: a teammate, and the one file it may write its report to."""

    name: str
    teammate: str
    report_path: str


@dataclass(frozen=True)
class JobResult:
    name: str
    status: Literal["succeeded", "failed"]
    lane: int | None = None
    summary: str = ""
    error: str = ""
    report_path: str = ""


# Runs one job with the tools it may use and its lane; returns the agent's summary.
ReadOnlyRunner = Callable[[ReadOnlyJob, list[BaseTool], int], str]


def readonly_tools(ctx: RunContext, job: ReadOnlyJob, lane: int | None = None) -> list[BaseTool]:
    """The tools of a read-only job: every read-only tool, plus write tools that can touch
    nothing but the job's own report path (the write scope refuses every other path)."""

    reading = build_tools(ctx, read_only=True, agent=job.teammate, lane=lane)
    have = {tool.name for tool in reading}
    writing = build_tools(
        ctx,
        groups=["fs_write"],
        write_scope=WriteScope(allow=(job.report_path,)),
        agent=job.teammate,
        lane=lane,
    )
    return [*reading, *(tool for tool in writing if tool.name not in have)]


def run_parallel_readonly(
    ctx: RunContext,
    jobs: Sequence[ReadOnlyJob],
    runner: ReadOnlyRunner,
    *,
    max_parallel: int | None = None,
) -> list[JobResult]:
    """Run read-only jobs (reviewers, codebase analysts) side by side; results are in job order.

    Each job gets :func:`readonly_tools`; a failing job is reported ``failed`` and the others
    finish. Raises ``ValueError`` for duplicate job names or report paths.
    """

    names = [job.name for job in jobs]
    paths = [job.report_path.strip("/") for job in jobs]
    if len(set(names)) != len(names) or len(set(paths)) != len(paths):
        raise ValueError("Read-only jobs need distinct names and distinct report paths.")
    limit = max_parallel or ctx.settings.parallel.max_parallel_agents

    def work(job: ReadOnlyJob, lane: int) -> JobResult:
        ctx.events.emit("lane.started", lane=lane, kind="job", unit=job.name)
        try:
            summary = runner(job, readonly_tools(ctx, job, lane), lane)
        except (RunCancelled, BudgetExceeded):
            ctx.events.emit("lane.finished", lane=lane, unit=job.name, status="cancelled")
            raise
        except Exception as exc:
            error = _clip(exc)
            ctx.events.emit("lane.finished", lane=lane, unit=job.name, status="failed", error=error)
            return JobResult(job.name, "failed", lane, error=error, report_path=job.report_path)
        ctx.events.emit("lane.finished", lane=lane, unit=job.name, status="succeeded")
        return JobResult(job.name, "succeeded", lane, summary, report_path=job.report_path)

    results = _fan_out(ctx, jobs, work, limit)
    return [
        r if isinstance(r, JobResult) else JobResult(job.name, "failed", error=_clip(r))
        for job, r in zip(jobs, results, strict=True)
    ]
