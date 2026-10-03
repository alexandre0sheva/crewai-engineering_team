"""Commands for a run in another process: a note, pause, and unpause reach its board."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from cli_helpers import latest_run, new_args, use_runner
from pipeline_fakes import FakeRunner

from engineering_team import main
from engineering_team.board.models import USER
from engineering_team.pipeline.stages import StageRequest
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.inbox import (
    apply_pending,
    inbox_dir,
    inbox_watch,
    pending_commands,
    post_command,
)
from engineering_team.runtime.run_index import locate_run

MakeContext = Callable[..., RunContext]


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def test_commands_apply_in_the_order_they_were_posted(make_context: MakeContext) -> None:
    ctx = make_context()
    post_command(ctx.run_dir, "pause")
    post_command(ctx.run_dir, "note", text="First thought.")
    post_command(ctx.run_dir, "unpause")

    assert apply_pending(ctx) == 3

    assert not ctx.board.paused and pending_commands(ctx.run_dir) == []
    notes = [c for c in ctx.board.cards() if c.kind == "user_note"]
    assert [comment.text for note in notes for comment in note.comments] == ["First thought."]


def test_a_note_for_a_card_becomes_a_comment_by_the_user(make_context: MakeContext) -> None:
    ctx = make_context()
    card = ctx.board.create_card("Storage layer", kind="work_package", assignee="backend_engineer")
    post_command(ctx.run_dir, "note", text="Use sqlite.", card=card.id)

    apply_pending(ctx)

    comment = ctx.board.get(card.id).comments[-1]
    assert (comment.author, comment.text) == (USER, "Use sqlite.")
    assert ctx.board.take_steering("backend_engineer") == f"User note on {card.id}: Use sqlite."


def test_bad_commands_are_dropped_with_an_event_and_never_crash_the_run(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    directory = inbox_dir(ctx.run_dir)
    directory.mkdir(parents=True)
    (directory / "01-garbage.json").write_text("{not json", encoding="utf-8")
    (directory / "02-list.json").write_text("[1, 2]", encoding="utf-8")
    (directory / "03-unknown.json").write_text('{"kind": "reboot"}', encoding="utf-8")
    (directory / "04-card.json").write_text(
        '{"kind": "note", "text": "x", "card": "K-404"}', encoding="utf-8"
    )
    (directory / "05-huge.json").write_text("x" * 30_000, encoding="utf-8")
    post_command(ctx.run_dir, "pause")

    assert apply_pending(ctx) == 1  # only the real one

    assert ctx.board.paused and pending_commands(ctx.run_dir) == []
    events = (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert events.count("inbox.rejected") == 5


def test_posting_validates_what_it_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown command"):
        post_command(tmp_path, "explode")
    with pytest.raises(ValueError, match="needs some text"):
        post_command(tmp_path, "note", text="  ")


def test_a_running_run_picks_up_commands_without_being_asked(make_context: MakeContext) -> None:
    ctx = make_context()

    with inbox_watch(ctx):
        post_command(ctx.run_dir, "pause")
        assert wait_for(lambda: ctx.board.paused)
        post_command(ctx.run_dir, "unpause")
        assert wait_for(lambda: not ctx.board.paused)


def test_commands_posted_before_a_run_starts_are_applied_when_it_does(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    post_command(ctx.run_dir, "note", text="Read this first.")

    with inbox_watch(ctx):
        assert any(c.kind == "user_note" for c in ctx.board.cards())


def test_a_note_posted_from_the_cli_while_a_run_works_reaches_the_next_teammate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def note_during_plan(request: StageRequest) -> None:
        if request.stage.name == "plan":
            assert (
                main.run(
                    [
                        "note",
                        request.ctx.run_id,
                        "Prefer sqlite.",
                        "--workspace-root",
                        str(request.ctx.workspace.root.parent),
                    ]
                )
                == 0
            )
            assert wait_for(lambda: any(c.kind == "user_note" for c in request.ctx.board.cards()))

    runner = use_runner(monkeypatch, FakeRunner(on_call=note_during_plan))

    assert main.run(new_args()) == 0

    steering = {
        call: request.steering for call, request in zip(runner.calls, runner.requests, strict=True)
    }
    assert steering[("plan", None)] == ""  # it arrived while the architect was working
    assert steering[("foundation", None)] == "User note: Prefer sqlite."


def test_pause_from_the_cli_holds_the_run_until_unpaused(monkeypatch: pytest.MonkeyPatch) -> None:
    stamps: dict[str, float] = {}

    def pause_then_release(request: StageRequest) -> None:
        stamps[request.stage.name] = time.monotonic()
        if request.stage.name == "spec":
            root = str(request.ctx.workspace.root.parent)
            assert main.run(["pause", request.ctx.run_id, "--workspace-root", root]) == 0
            assert wait_for(lambda: request.ctx.board.paused)
            from threading import Timer

            Timer(
                0.5, main.run, [["unpause", request.ctx.run_id, "--workspace-root", root]]
            ).start()

    use_runner(monkeypatch, FakeRunner(on_call=pause_then_release))

    assert main.run(new_args()) == 0

    assert stamps["plan"] - stamps["spec"] >= 0.4


def test_a_run_is_found_by_id_prefix_across_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    use_runner(monkeypatch)
    assert main.run(new_args()) == 0
    run_id = latest_run()
    root = Path.cwd() / "ws"

    assert locate_run(root, run_id[:10]).run_id == run_id
    assert locate_run(root).run_id == run_id
    assert locate_run(root, run_id, "demo").project == "demo"
    with pytest.raises(ValueError, match="No run 'nope'"):
        locate_run(root, "nope")
    with pytest.raises(ValueError, match="No runs found"):
        locate_run(Path.cwd() / "empty-root")
