"""The programmatic API: continue an unfinished run."""

from __future__ import annotations

import sys
from pathlib import Path

from engineering_team.pipeline.runner import (
    execute_run,
    open_resume,
    read_request,
)
from engineering_team.pipeline.state import RunResult, request_hash
from engineering_team.run_prep import PreparedRun, _open_run
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import (
    Settings,
    load_settings,
)


def _prepare_resume(
    settings: Settings, run_id: str, request: str | None, workspace: Path | None = None
) -> PreparedRun:
    """Reopen ``run_id``, or, when ``request`` differs from the one it was started with, start a
    new run for that request (old evidence is never reused for a different request)."""

    settings.check_ready(require_credentials=True)
    _, manifest, _ = read_request(settings, run_id, workspace)
    if request is not None and request_hash(request) != manifest.request_hash:
        if manifest.mode != "build":
            raise ValueError(
                f"The request changed since run {run_id}. A {manifest.mode} run cannot be "
                f"restarted from a new request; run `engineering-team {manifest.mode}` again."
            )
        print(
            f"The request changed since run {run_id}; starting a new run instead of resuming it.",
            file=sys.stderr,
        )
        fresh = settings.for_request(request).with_overrides(
            {"strategy": manifest.strategy}, source=f"changed request (was run {run_id})"
        )
        return _open_run(fresh, mode="build", requirements=request)
    opened = open_resume(settings, run_id, workspace)
    return PreparedRun(
        ctx=opened.ctx,
        inputs={},
        lock=opened.lock,
        recorder=RunRecorder.attach(opened.ctx),
        bundle=opened.bundle,
        strategy=opened.strategy,
        recipe=opened.recipe,
    )


def resume(
    run_id: str, *, settings: Settings | None = None, request: str | None = None
) -> RunResult:
    """Continue an unfinished or failed pipeline run and return how it ended.

    Stages that finished (and whose workspace state still matches) are not run again. Passing a
    ``request`` that differs from the run's own starts a new run for it instead. Raises
    ``ValueError`` when the run cannot be resumed (unknown, already succeeded, a retired strategy,
    recipe changed) and ``WorkspaceBusy`` when another run holds the workspace.
    """

    prepared = _prepare_resume(settings or load_settings(), run_id, request)
    try:
        return execute_run(
            prepared.ctx, prepared.bundle, strategy=prepared.strategy, recipe=prepared.recipe
        )
    finally:
        prepared.lock.release()
