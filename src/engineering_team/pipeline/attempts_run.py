"""One attempt of a best-of-N run (``attempts.py``): a run of its own, in a copy of the workspace.

An attempt is a complete run (manifest, board, events, verification, report) with the id
``<run-id>-a<k>``, made in its copy (``attempts_tree.py``) and moved to
``runs/<run-id>/attempts/<label>/run`` when it ends. It walks the same recipe as its siblings
with the same request, pinned checks and lessons; what differs is ``attempts.vary``:

* ``seed``: nothing but the attempt's own sampling (the attempt number is recorded);
* ``model``: the worker models, taken in turn from the ``worker``, ``reviewer`` and ``lead``
  tiers (the lead keeps its own);
* ``harness``: the run's harness, the same domain from the bundled library only (no user or
  repository overlays), and the bundled ``base`` domain, in turn.

Its model calls and tool calls are also sent to the run's own event log (stage ``attempts``,
lane ``<label>``), so the run's usage, cost and budget cover every attempt: the run's budget
guard stops all of them, and each attempt also has its share of what the budget had left. Its
hooks are switched off (the run's own fire once), and it never reflects on itself.
"""

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from engineering_team.contracts import utc_now
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import ResolvedHarness, resolve
from engineering_team.learning.retrieve import LESSONS_FILE
from engineering_team.pipeline.attempts_contracts import ATTEMPTS_DIR, AttemptRecord
from engineering_team.pipeline.attempts_tree import TreeDiff, make_copy, tree_diff
from engineering_team.pipeline.flow import run_flow
from engineering_team.pipeline.ladder_brief import failing_checks
from engineering_team.pipeline.recipes import Recipe
from engineering_team.pipeline.stages import StageRunner
from engineering_team.pipeline.state import STATE_FILENAME, PipelineState, RunBundle
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import EventSink, FanoutSink, JsonlSink, Scrubber
from engineering_team.runtime.run_store import EVENTS_FILENAME
from engineering_team.runtime.session import RunRecorder
from engineering_team.runtime.snapshot import Snapshot, workspace_revision
from engineering_team.settings import Settings, secret_values
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace
from engineering_team.verification.checks_file import PINNED_FILENAME

ATTEMPTS_STAGE = "attempts"  # the run's stage that holds the attempts
FORWARDED = ("llm.call", "tool.call")  # what the run's own log also gets from an attempt
MODEL_TIERS = ("worker", "reviewer", "lead")
RUN_FILES = (PINNED_FILENAME, LESSONS_FILE)  # what an attempt's run directory starts with
NO_HOOKS: dict[str, list[str]] = {
    "hooks.before_stage": [],
    "hooks.after_stage": [],
    "hooks.on_finish": [],
}


@dataclass
class Attempt:
    """One attempt while it runs: its record, and what the controller saw when it ended."""

    record: AttemptRecord
    settings: Settings
    harness: ResolvedHarness
    workspace: ProjectWorkspace | None = None
    state: PipelineState | None = None
    diff: TreeDiff = dataclasses.field(default_factory=TreeDiff)
    child: RunContext | None = None


class Forward:
    """Sends an attempt's model and tool calls on to the run's own log, tagged with it."""

    def __init__(self, sink: EventSink, label: str) -> None:
        self._sink, self._label = sink, label

    def emit(self, type: str, **data: Any) -> None:
        if type in FORWARDED:
            data.update(stage=ATTEMPTS_STAGE, lane=self._label, attempt=self._label)
            self._sink.emit(type, **data)


class CancelLink:
    """Sets every attempt's cancel event once the run's is set (a cancel, or its budget guard)."""

    def __init__(self, parent: threading.Event) -> None:
        self._parent = parent
        self._children: list[threading.Event] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="attempts-cancel", daemon=True)

    def add(self, event: threading.Event) -> None:
        with self._lock:
            self._children.append(event)
            if self._parent.is_set():
                event.set()

    def __enter__(self) -> CancelLink:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._done.set()
        self._thread.join()

    def _watch(self) -> None:
        while not self._done.wait(0.2):
            if self._parent.is_set():
                with self._lock:
                    for event in self._children:
                        event.set()
                return


def variant(
    ctx: RunContext, number: int, share_usd: float | None
) -> tuple[Settings, ResolvedHarness, str]:
    """The settings, harness and description of attempt ``number`` (1-based)."""

    settings = ctx.settings
    overrides: dict[str, Any] = {**NO_HOOKS, "attempts.count": 1}
    if share_usd is not None:
        overrides["budget.max_cost_usd"] = round(max(share_usd, 0.0), 6)
    harness = dataclasses.replace(ctx.harness, loaded=[], _lock=threading.Lock())
    vary = settings.attempts.vary
    described = f"seed {number}"
    if vary == "model":
        tier = MODEL_TIERS[(number - 1) % len(MODEL_TIERS)]
        model = settings.resolve_model("attempt", tier=tier).model
        if tier != "worker":
            overrides[f"profiles.{settings.profile}.worker.model"] = model
        described = f"worker model {model} (the {tier} tier's)"
    elif vary == "harness":
        which = (number - 1) % 3
        if which == 1:
            harness = resolve(ctx.harness.domain)
            described = f"harness {harness.domain.key}, bundled library only"
        elif which == 2:
            harness = resolve(Domain())
            described = "harness of the base domain (no domain packs), bundled library only"
        else:
            described = f"harness {harness.domain.key}, as the run resolved it"
    return settings.with_overrides(overrides, source=f"attempt {number}"), harness, described


def run_one(
    ctx: RunContext,
    attempt: Attempt,
    recipe: Recipe,
    bundle: RunBundle,
    runner: StageRunner,
    *,
    start: Snapshot,
    copy_to: Path,
    mode: str,
    strategy: str,
    link: CancelLink,
) -> Attempt:
    """Run ``attempt`` to its end; never raises for what happens inside the attempt's run."""

    record = attempt.record
    record.started, record.status = utc_now(), "running"
    status, error = "failed", ""
    try:
        child = _open(ctx, attempt, copy_to, mode, strategy, recipe, bundle.requirements)
        link.add(child.cancel_event)
        recorder = RunRecorder.attach(child)
        with recorder.running():
            status, error = run_flow(child, recipe, replace(bundle, resume=False), runner)
            if status == "failed":
                recorder.fail(error or "The attempt failed.")
        status = recorder.manifest.status
    except Exception as exc:  # a budget stop, a cancel or a crash ends this attempt only
        error = f"{type(exc).__name__}: {exc}"
        if attempt.child is not None:
            with contextlib.suppress(Exception):  # no manifest: the attempt never started
                status = RunRecorder.attach(attempt.child).manifest.status
    _close(attempt, start, status, error)
    return attempt


def _open(
    ctx: RunContext,
    attempt: Attempt,
    copy_to: Path,
    mode: str,
    strategy: str,
    recipe: Recipe,
    request: str,
) -> RunContext:
    record = attempt.record
    workspace = make_copy(ctx.workspace, copy_to, attempt.settings)
    attempt.workspace = workspace
    run_dir = workspace.root / CONTROLLER_DIRECTORY / "runs" / record.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sink = FanoutSink(
        JsonlSink(run_dir / EVENTS_FILENAME, record.run_id, scrubber=Scrubber(secret_values())),
        Forward(ctx.events, record.label),
    )
    child = RunContext.create(
        attempt.settings,
        workspace,
        run_id=record.run_id,
        events=sink,
        adopted=not ctx.reports.in_project,
        harness=attempt.harness,
    )
    attempt.child = child
    for name in RUN_FILES:  # the run's pinned checks and chosen lessons, as they are
        source = ctx.run_dir / name
        if source.is_file():
            shutil.copyfile(source, child.run_dir / name)
    RunRecorder.begin(
        child,
        mode=mode,
        request=request,
        strategy=strategy,
        recipe=recipe.name,
    )
    return child


def _close(attempt: Attempt, start: Snapshot, status: str, error: str) -> None:
    """Record how the attempt ended, from its run's own files (not from what its agents said)."""

    record = attempt.record
    record.finished = utc_now()
    child = attempt.child
    state = PipelineState.load(child.run_dir) if child is not None else None
    attempt.state = state
    if state is not None:
        required = [check for check in state.checks if check.required]
        record.checks_total = len(required)
        record.checks_passed = sum(1 for check in required if check.status == "passed")
        record.failing_checks = failing_checks(state)
        record.findings = len(state.findings)
        record.verdict = state.verification.verdict
    passed = status == "succeeded" and record.verdict in (None, "verified")
    record.status = "passed" if passed else "failed"
    record.outcome = (
        f"succeeded ({record.verdict or 'no checks'})" if passed else f"{status}: {error}"[:300]
    )
    if child is not None:
        record.cost_usd = child.usage.report(child.prices).estimated_cost_usd
    if attempt.workspace is not None:
        attempt.diff = tree_diff(start, attempt.workspace)
        record.files_changed, record.diff_lines = attempt.diff.files, attempt.diff.lines
        record.revision = workspace_revision(attempt.workspace)


def keep(ctx: RunContext, attempt: Attempt) -> None:
    """Keep the attempt's patch and run directory in the run's ``attempts/`` directory."""

    record = attempt.record
    folder = ctx.run_dir / ATTEMPTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    patch = folder / f"{record.label}.patch"
    patch.write_text(attempt.diff.patch, encoding="utf-8")
    record.patch = f"{ATTEMPTS_DIR}/{patch.name}"
    child = attempt.child
    if child is None or not child.run_dir.is_dir():
        return
    target = folder / record.label / "run"
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(child.run_dir), str(target))
    record.run_dir = f"{ATTEMPTS_DIR}/{record.label}/run"


def adopt_state(ctx: RunContext, attempt: Attempt) -> None:
    """The chosen attempt's final state becomes the run's (its verdict, checks and findings)."""

    if attempt.record.run_dir:
        source = ctx.run_dir / attempt.record.run_dir / STATE_FILENAME
        if source.is_file():
            shutil.copyfile(source, ctx.run_dir / STATE_FILENAME)
