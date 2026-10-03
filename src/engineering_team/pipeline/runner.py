"""Starting, resuming, and cancelling runs: the lifecycle around a strategy.

``execute_run`` is what every entry point calls once a run is open: it wraps the strategy in the
run recorder (``pending → running → end state``) and in cancellation handling (SIGINT/SIGTERM
and the ``cancel`` flag file). ``open_resume`` reopens a run that did not finish, and
``request_run_cancel`` asks a run in another process to stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering_team.contracts import RunManifest
from engineering_team.pipeline.recipes import Recipe, load_recipe
from engineering_team.pipeline.resume import ResumeError
from engineering_team.pipeline.state import (
    PipelineState,
    RunBundle,
    RunResult,
    request_hash,
)
from engineering_team.pipeline.strategies import Strategy, get_strategy
from engineering_team.runtime.cancel import cancellation, request_cancel
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.locks import WorkspaceBusy, WorkspaceLock
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import Settings
from engineering_team.verification.checks_file import digest_of
from engineering_team.workspaces import (
    prepare_workspace,
    resolve_workspace_root,
    slugify_project_name,
)


def execute_run(
    ctx: RunContext, bundle: RunBundle, *, strategy: Strategy, recipe: Recipe | None
) -> RunResult:
    """Run ``strategy`` for an open run and return how it ended (as the manifest records it).

    The caller owns the workspace lock and has created (or reopened) the manifest. A strategy
    that fails without raising ends the run ``failed``; one that raises ends it ``failed``
    (``interrupted`` for Ctrl-C) and the exception propagates.
    """

    recorder = RunRecorder.attach(ctx)
    with cancellation(ctx), recorder.running():
        result = strategy.run(ctx, recipe, bundle)
        if result.verdict is not None:
            recorder.set_verdict(result.verdict)
        if result.status == "failed":
            recorder.fail(result.error or "The run failed.")
    manifest = recorder.manifest
    error = (result.error if manifest.status == "failed" else "") or ""
    return RunResult(
        run_id=ctx.run_id,
        status=manifest.status,
        error=error,
        stages=manifest.stages,
        workspace=ctx.workspace.root,
        verdict=manifest.verdict,
    )


@dataclass
class ResumedRun:
    """A reopened run: its context, the lock held on the workspace, and what to run."""

    ctx: RunContext
    lock: WorkspaceLock
    bundle: RunBundle
    strategy: Strategy
    recipe: Recipe | None
    manifest: RunManifest

    def release(self) -> None:
        self.lock.release()


def read_request(settings: Settings, run_id: str) -> tuple[RunStore, RunManifest, str]:
    """The run's manifest and the request it was started with (checked against its hash)."""

    workspace_dir = _workspace_dir(settings)
    store = RunStore(workspace_dir)
    manifest = store.load(run_id)  # RunNotFound is a ValueError: a usage error
    path = store.run_dir(run_id) / "request.md"
    try:
        request = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise ResumeError(f"Run {run_id} has no request.md, so it cannot be resumed.") from None
    if request_hash(request) != manifest.request_hash:
        raise ResumeError(f"{path} was modified after the run started; start a new run instead.")
    return store, manifest, request


def open_resume(settings: Settings, run_id: str) -> ResumedRun:
    """Reopen an unfinished or failed run of the pipeline (or single-agent) strategy.

    Raises ``ValueError`` (a usage error) when the run cannot be resumed: it already
    succeeded, used the hierarchical strategy, or its recipe changed.
    """

    store, manifest, request = read_request(settings, run_id)
    if manifest.mode != "build":
        raise ResumeError(f"Run {run_id} is a '{manifest.mode}' run; only builds can be resumed.")
    if manifest.status == "succeeded":
        raise ResumeError(f"Run {run_id} already succeeded; there is nothing to resume.")
    strategy = get_strategy(manifest.strategy)
    if not strategy.resumable:
        raise ResumeError(
            f"Run {run_id} used the '{manifest.strategy}' strategy, which cannot be resumed. "
            "Start a new run (or use --strategy pipeline, which can be resumed)."
        )
    # The single-agent strategy supplies its own one-stage recipe; the pipeline loads its own.
    recipe = (
        load_recipe(manifest.recipe)
        if manifest.strategy == "pipeline" and manifest.recipe
        else None
    )
    effective = strategy.recipe_for(recipe)
    if effective is None:  # only a strategy without a recipe, and it is not resumable
        raise ResumeError(f"Run {run_id} has no recipe to resume.")
    saved = PipelineState.load(store.run_dir(run_id))
    if saved is not None and saved.recipe_digest != effective.digest:
        raise ResumeError(
            f"Recipe '{effective.name}' changed since run {run_id} started; resuming would mix "
            "two plans. Start a new run instead."
        )
    _check_same_checks(settings, saved)
    workspace = prepare_workspace(
        settings.project_name,
        settings.workspace_root,
        command_allowlist=settings.command_allowlist,
        subprocess_env_allowlist=settings.subprocess_env_allowlist,
    )
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    try:
        ctx = RunContext.create(settings, workspace, run_id=run_id, resume=True)
        RunRecorder.reopen(ctx)
    except BaseException:
        lock.release()
        raise
    bundle = RunBundle(requirements=request, resume=True)
    return ResumedRun(ctx, lock, bundle, strategy, recipe, store.load(run_id))


def _check_same_checks(settings: Settings, saved: PipelineState | None) -> None:
    """A resume may repeat ``--checks FILE``, but only the file the run started with."""

    source = settings.verify.checks_file
    if source is None or saved is None:
        return
    try:
        text = Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ResumeError(f"Cannot read the checks file {source}: {exc}") from exc
    if digest_of(text) != saved.verification.checks_digest:
        raise ResumeError(
            f"{source} is not the checks file this run started with, so its results would not "
            "be comparable. Resume without --checks (the run keeps its own copy), or start a "
            "new run."
        )


def request_run_cancel(settings: Settings, run_id: str) -> str:
    """Ask a run in another process to stop; returns what to tell the user."""

    root = _workspace_dir(settings)
    manifest = RunStore(root).load(run_id)
    if manifest.status not in ("pending", "running"):
        return f"Run {run_id} is already {manifest.status}; nothing to cancel."
    try:
        WorkspaceLock(root).acquire("cancel-probe").release()
    except WorkspaceBusy:
        request_cancel(root, run_id)
        return (
            f"Cancellation requested for run {run_id}. It stops at its next safe point and "
            f"ends as 'cancelled'; continue it later with: engineering-team resume {run_id}"
        )
    return (
        f"Run {run_id} is marked {manifest.status} but no process is working on it (it was "
        f"probably killed). Continue it with: engineering-team resume {run_id}"
    )


def _workspace_dir(settings: Settings) -> Path:
    path = resolve_workspace_root(settings.workspace_root) / slugify_project_name(
        settings.project_name
    )
    if not path.is_dir():
        raise ValueError(
            f"No project workspace at {path}. Pass --project-name and --workspace-root of the run."
        )
    return path
