"""Cancelling runs: signals, the flag file, the ``cancel`` command, and cooperative stops."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from pipeline_fakes import REQUEST, FakeRunner, settings_for
from test_pipeline_flow import ROOT, only_run, project, resume, run_dir, start, use_runner

from engineering_team import main
from engineering_team.contracts import RunManifest
from engineering_team.pipeline.runner import request_run_cancel
from engineering_team.pipeline.stages import StageRequest
from engineering_team.runtime.cancel import (
    RunCancelled,
    cancel_flag_path,
    cancellation,
    check_cancelled,
    request_cancel,
)
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.run_store import RunNotFound, RunStore
from engineering_team.runtime.session import RunRecorder

MakeContext = Callable[..., RunContext]


def _opened(make_context: MakeContext) -> RunContext:
    ctx = make_context()
    RunRecorder.begin(ctx, request=REQUEST)
    return ctx


def wait_for(event: threading.Event, seconds: float = 5.0) -> bool:
    return event.wait(seconds)


# -- the primitives ------------------------------------------------------------------------------


def test_the_cancel_flag_file_stops_the_run(make_context: MakeContext) -> None:
    ctx = _opened(make_context)

    with cancellation(ctx):
        assert not ctx.cancel_event.is_set()
        flag = request_cancel(ctx.workspace.root, ctx.run_id)
        assert flag == cancel_flag_path(ctx.workspace.root, ctx.run_id)
        assert wait_for(ctx.cancel_event)

    events = [
        e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "run.cancel_requested"
    ]
    assert events and events[0].data["via"] == "cancel flag"


def test_a_flag_left_by_an_earlier_session_does_not_cancel_a_resumed_run(
    make_context: MakeContext,
) -> None:
    ctx = _opened(make_context)
    request_cancel(ctx.workspace.root, ctx.run_id)

    with cancellation(ctx):
        assert not ctx.cancel_event.wait(1.2)

    assert not cancel_flag_path(ctx.workspace.root, ctx.run_id).exists()


def test_cancelling_an_unknown_run_is_a_clear_error(make_context: MakeContext) -> None:
    ctx = make_context()

    with pytest.raises(RunNotFound):
        request_cancel(ctx.workspace.root, "20200101-000000-nothing")


def test_sigint_cancels_gracefully_and_a_second_one_stops_at_once(
    make_context: MakeContext, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _opened(make_context)
    before = signal.getsignal(signal.SIGINT)

    with cancellation(ctx):
        signal.raise_signal(signal.SIGINT)
        assert ctx.cancel_event.is_set()
        assert "press Ctrl-C again" in capsys.readouterr().err
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)

    assert signal.getsignal(signal.SIGINT) is before


def test_sigterm_cancels_and_handlers_are_restored(make_context: MakeContext) -> None:
    ctx = _opened(make_context)
    before = signal.getsignal(signal.SIGTERM)

    with cancellation(ctx):
        signal.raise_signal(signal.SIGTERM)
        assert ctx.cancel_event.is_set()

    assert signal.getsignal(signal.SIGTERM) is before


def test_check_cancelled_raises_for_a_cancel_and_for_a_budget_stop_differently(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    check_cancelled(ctx)  # nothing to do

    ctx.cancel_event.set()
    with pytest.raises(RunCancelled):
        check_cancelled(ctx)


# -- through the pipeline ------------------------------------------------------------------------


def test_cancelling_from_another_process_stops_the_run_and_it_resumes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``engineering-team cancel`` while a stage is working: the stage ends, the run is
    ``cancelled`` (exit 130), and ``resume`` finishes it."""

    def cancel_during_plan(request: StageRequest) -> None:
        if request.stage.name == "plan":
            code = main.run(
                [
                    "cancel",
                    request.ctx.run_id,
                    "--project-name",
                    "demo",
                    "--workspace-root",
                    str(Path.cwd() / ROOT),
                ]
            )
            assert code == 0
            assert request.ctx.cancel_event.wait(5), "the flag file was not noticed"

    use_runner(monkeypatch, FakeRunner(on_call=cancel_during_plan))

    assert start() == 130

    manifest = only_run()
    assert manifest.status == "cancelled"
    assert {r.name: r.status for r in manifest.stages} == {"spec": "succeeded", "plan": "cancelled"}
    out = capsys.readouterr()
    assert "Cancellation requested" in out.out and "engineering-team resume" in out.err
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(manifest.run_id) == 0

    assert runner.calls[0] == ("plan", None) and ("spec", None) not in runner.calls
    assert not cancel_flag_path(project(), manifest.run_id).exists()


def test_a_cancel_while_paused_between_stages_starts_no_further_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pause_then_cancel(request: StageRequest) -> None:
        board = request.ctx.board
        board.pause()
        threading.Timer(0.3, request.ctx.cancel_event.set).start()

    runner = use_runner(monkeypatch, FakeRunner(on_call=pause_then_cancel))

    assert start() == 130

    assert runner.calls == [("spec", None)]  # the run waited at the boundary, then stopped
    manifest = only_run()
    assert manifest.status == "cancelled"
    assert [(r.name, r.status) for r in manifest.stages] == [("spec", "succeeded")]
    resumed = use_runner(monkeypatch, FakeRunner())
    assert resume(manifest.run_id) == 0
    assert resumed.calls[0] == ("plan", None) and ("spec", None) not in resumed.calls


def test_the_cancel_command_explains_every_situation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"spec": 2}))
    start()
    run_id = only_run().run_id
    settings = settings_for(Path.cwd() / ROOT)

    # Finished runs (failed counts) have nothing to cancel.
    assert "already failed" in request_run_cancel(settings, run_id)

    # A run marked running whose process is gone.
    def as_if_killed(manifest: RunManifest) -> None:
        manifest.status = "running"
        manifest.finished = None

    RunStore(project()).update(run_id, as_if_killed)
    message = request_run_cancel(settings, run_id)
    assert "no process is working on it" in message and f"resume {run_id}" in message
    assert not cancel_flag_path(project(), run_id).exists()

    # A live holder of the workspace: the flag is written.
    lock = WorkspaceLock(project()).acquire(run_id)
    try:
        assert "Cancellation requested" in request_run_cancel(settings, run_id)
        assert cancel_flag_path(project(), run_id).is_file()
    finally:
        lock.release()

    with pytest.raises(ValueError, match="No run"):
        request_run_cancel(settings, "20200101-000000-nothing")
    with pytest.raises(ValueError, match="No project workspace"):
        request_run_cancel(settings_for(Path.cwd() / "elsewhere"), run_id)


def test_the_cancel_command_reports_usage_errors_on_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main.run(
        ["cancel", "20200101-000000-nothing", "--workspace-root", str(Path.cwd() / ROOT)]
    )

    assert code == 2
    assert "No project workspace" in capsys.readouterr().err


def test_a_cancelled_run_keeps_a_truthful_board(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team.board.store import BoardStore

    use_runner(monkeypatch, FakeRunner(cancel_at="foundation"))
    assert start() == 130

    board = BoardStore(run_dir(only_run()))
    statuses = {card.stage: card.status for card in board.cards(kind="stage")}
    assert statuses["spec"] == statuses["plan"] == "done"
    assert statuses["foundation"] == "cancelled"
    assert statuses["implement"] == statuses["verify"] == statuses["release"] == "backlog"
