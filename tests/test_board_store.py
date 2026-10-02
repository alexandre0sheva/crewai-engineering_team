"""The task board store: transition rules, evidence, WIP, concurrency, events, steering, files."""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from engineering_team.board import (
    COLUMNS,
    CONTROLLER,
    USER,
    BoardError,
    BoardStore,
    CardStatus,
    allowed_moves,
)
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import load_settings

GOLDEN = Path(__file__).parent / "golden" / "board.md"
START = datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC)
ALL_STATUSES: tuple[CardStatus, ...] = (*COLUMNS, "cancelled")


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def emit(self, type: str, **data: Any) -> None:
        with self._lock:
            self.events.append((type, data))

    def of(self, type: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == type]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sink() -> Recorder:
    return Recorder()


@pytest.fixture
def board(tmp_path: Path, sink: Recorder, clock: Clock) -> BoardStore:
    return BoardStore(
        tmp_path, sink, run_id="run-1", max_in_progress=2, clock=clock, render_delay=0
    )


def _card_in(board: BoardStore, status: CardStatus, *, assignee: str | None = "dev") -> str:
    """A work package that has been walked to ``status`` by the controller."""

    card = board.create_card("Work", kind="work_package", assignee=assignee, stage="build")
    path = {
        "backlog": [],
        "ready": ["ready"],
        "in_progress": ["ready", "in_progress"],
        "verifying": ["ready", "in_progress", "verifying"],
        "blocked": ["ready", "in_progress", "blocked"],
        "done": ["ready", "in_progress", "done"],
        "failed": ["ready", "failed"],
        "cancelled": ["cancelled"],
    }[status]
    for step in path:
        board.move(card.id, step, actor=CONTROLLER, reason="r", evidence=["c1"])  # type: ignore[arg-type]
    return card.id


# -- the transition rules --------------------------------------------------------------


AGENT_ALLOWED = {
    ("ready", "in_progress"),
    ("backlog", "blocked"),
    ("ready", "blocked"),
    ("in_progress", "blocked"),
    ("verifying", "blocked"),
    ("in_progress", "verifying"),
    ("blocked", "in_progress"),
}
CONTROLLER_ONLY = {
    ("backlog", "ready"),
    ("backlog", "failed"),
    ("backlog", "cancelled"),
    ("ready", "failed"),
    ("ready", "cancelled"),
    ("in_progress", "done"),
    ("in_progress", "failed"),
    ("in_progress", "cancelled"),
    ("verifying", "in_progress"),
    ("verifying", "done"),
    ("verifying", "failed"),
    ("verifying", "cancelled"),
    ("blocked", "ready"),
    ("blocked", "failed"),
    ("blocked", "cancelled"),
}


def test_the_transition_matrix_agents_versus_controller() -> None:
    for before, after in itertools.product(ALL_STATUSES, repeat=2):
        agent_ok = after in allowed_moves(before, "dev")
        controller_ok = after in allowed_moves(before, CONTROLLER)
        assert agent_ok == ((before, after) in AGENT_ALLOWED), (before, after)
        assert controller_ok == ((before, after) in AGENT_ALLOWED | CONTROLLER_ONLY), (
            before,
            after,
        )


@pytest.mark.parametrize(("before", "after"), sorted(AGENT_ALLOWED))
def test_an_agent_makes_every_move_the_table_gives_it(
    board: BoardStore, before: CardStatus, after: CardStatus
) -> None:
    card_id = _card_in(board, before)

    moved = board.move(card_id, after, actor="dev", reason="because")

    assert moved.status == after


@pytest.mark.parametrize(("before", "after"), sorted(CONTROLLER_ONLY))
def test_an_agent_cannot_make_controller_only_moves(
    board: BoardStore, before: CardStatus, after: CardStatus
) -> None:
    card_id = _card_in(board, before)

    with pytest.raises(BoardError, match="Allowed next states"):
        board.move(card_id, after, actor="dev", reason="because", evidence=["c1"])
    assert board.get(card_id).status == before


@pytest.mark.parametrize(("before", "after"), sorted(CONTROLLER_ONLY))
def test_the_controller_makes_its_moves(
    board: BoardStore, before: CardStatus, after: CardStatus
) -> None:
    card_id = _card_in(board, before)

    moved = board.move(card_id, after, actor=CONTROLLER, reason="because", evidence=["c1"])

    assert moved.status == after


def test_an_agent_cannot_mark_a_card_done_by_itself(board: BoardStore) -> None:
    card_id = _card_in(board, "in_progress")

    with pytest.raises(BoardError) as refused:
        board.move(card_id, "done", actor="dev", evidence=["c1"])

    message = str(refused.value)
    assert "Allowed next states: verifying, blocked" in message
    assert "Only the controller marks cards done" in message
    assert board.get(card_id).status == "in_progress"


def test_terminal_cards_are_final(board: BoardStore) -> None:
    for status in ("done", "failed", "cancelled"):
        card_id = _card_in(board, status)  # type: ignore[arg-type]
        with pytest.raises(BoardError, match="the card is final"):
            board.move(card_id, "in_progress", actor=CONTROLLER)


def test_an_agent_can_only_move_its_own_cards(board: BoardStore) -> None:
    card_id = _card_in(board, "ready", assignee="backend")

    with pytest.raises(BoardError, match="assigned to backend, not to you"):
        board.move(card_id, "in_progress", actor="frontend")
    unassigned = _card_in(board, "ready", assignee=None)
    with pytest.raises(BoardError, match="not assigned to anyone"):
        board.move(unassigned, "in_progress", actor="frontend")


def test_done_needs_evidence_or_stage_success(board: BoardStore) -> None:
    card_id = _card_in(board, "verifying")

    with pytest.raises(BoardError, match="without evidence"):
        board.move(card_id, "done", actor=CONTROLLER)
    done = board.move(card_id, "done", actor=CONTROLLER, evidence=["tests", "lint"])
    assert done.evidence == ["tests", "lint"] and done.finished == START

    stage = board.create_card("Build", kind="stage", stage="Build", status="ready")
    board.move(stage.id, "in_progress", actor=CONTROLLER)
    finished = board.move(stage.id, "done", actor=CONTROLLER, stage_success=True)
    assert finished.history[-1].note == "stage succeeded"


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_blocking_and_failing_need_a_reason(board: BoardStore, status: CardStatus) -> None:
    card_id = _card_in(board, "in_progress")

    with pytest.raises(BoardError, match="needs a reason"):
        board.move(card_id, status, actor=CONTROLLER, reason="  ")


def test_blocking_records_the_reason_and_unblocking_clears_it(board: BoardStore) -> None:
    card_id = _card_in(board, "in_progress")

    blocked = board.move(card_id, "blocked", actor="dev", reason="needs the API key name")
    assert blocked.blocked_reason == "needs the API key name"

    resumed = board.move(card_id, "in_progress", actor="dev")
    assert resumed.blocked_reason is None and resumed.attempts == 1  # not a new attempt


def test_failed_checks_send_a_card_back_and_count_an_attempt(board: BoardStore) -> None:
    card_id = _card_in(board, "verifying")
    assert board.get(card_id).attempts == 1

    again = board.move(
        card_id, "in_progress", actor=CONTROLLER, reason="tests failed", evidence=["tests"]
    )

    assert again.attempts == 2
    assert again.history[-1].evidence == ["tests"]


def test_the_wip_limit_applies_to_work_cards_in_progress(board: BoardStore) -> None:
    first, second, third = (_card_in(board, "ready") for _ in range(3))
    board.move(first, "in_progress", actor="dev")
    board.move(second, "in_progress", actor="dev")

    with pytest.raises(BoardError, match=r"WIP limit reached: 2 cards .*limit 2"):
        board.move(third, "in_progress", actor="dev")
    board.move(first, "verifying", actor="dev")  # frees a slot
    assert board.move(third, "in_progress", actor="dev").status == "in_progress"


def test_stage_cards_and_subtasks_do_not_use_wip_slots(board: BoardStore) -> None:
    stage = board.create_card("Build", kind="stage", stage="Build", status="ready")
    board.move(stage.id, "in_progress", actor=CONTROLLER)
    first, second = (_card_in(board, "ready") for _ in range(2))
    board.move(first, "in_progress", actor="dev")
    board.move(second, "in_progress", actor="dev")

    sub = board.create_subtask(first, "Write the parser", actor="dev")

    assert board.move(sub.id, "in_progress", actor="dev").status == "in_progress"


def test_sending_a_card_back_is_not_blocked_by_the_wip_limit(board: BoardStore) -> None:
    first, second, third = (_card_in(board, "ready") for _ in range(3))
    board.move(first, "in_progress", actor="dev")
    board.move(first, "verifying", actor="dev")
    board.move(second, "in_progress", actor="dev")
    board.move(third, "in_progress", actor="dev")

    assert board.move(first, "in_progress", actor=CONTROLLER).attempts == 2


def test_unknown_cards_say_how_to_find_the_ids(board: BoardStore) -> None:
    with pytest.raises(BoardError, match="List Board Cards"):
        board.move("K-999", "ready", actor=CONTROLLER)


# -- concurrency -----------------------------------------------------------------------


def test_many_threads_moving_different_cards_lose_nothing(tmp_path: Path) -> None:
    board = BoardStore(tmp_path, max_in_progress=100, render_delay=0)
    ids = [board.create_card(f"c{i}", kind="subtask", status="ready").id for i in range(40)]
    errors: list[Exception] = []

    def work(card_id: str) -> None:
        try:
            for index in range(5):
                board.comment(card_id, f"note {index}", author="dev")
            board.move(card_id, "in_progress", actor=CONTROLLER)
            board.move(card_id, "done", actor=CONTROLLER, evidence=["c"])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(card_id,)) for card_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    cards = board.cards()
    assert [card.id for card in cards] == ids
    assert all(card.status == "done" and len(card.comments) == 5 for card in cards)
    saved = json.loads((tmp_path / "board.json").read_text(encoding="utf-8"))
    assert [card["status"] for card in saved["cards"]] == ["done"] * 40


def test_exactly_one_of_two_racing_moves_wins(tmp_path: Path) -> None:
    board = BoardStore(tmp_path, max_in_progress=5, render_delay=0)
    card_id = board.create_card("Contested", kind="work_package", status="ready", assignee="dev").id
    barrier = threading.Barrier(2)
    results: list[str] = []

    def race(to: CardStatus) -> None:
        barrier.wait(timeout=10)
        try:
            board.move(card_id, to, actor=CONTROLLER, reason="r", evidence=["c"])
            results.append("ok")
        except BoardError:
            results.append("refused")

    threads = [threading.Thread(target=race, args=(to,)) for to in ("failed", "cancelled")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == ["ok", "refused"]


def test_the_wip_limit_holds_under_concurrent_starts(tmp_path: Path) -> None:
    board = BoardStore(tmp_path, max_in_progress=2, render_delay=0)
    ids = [
        board.create_card(f"w{i}", kind="work_package", status="ready", assignee=f"a{i}").id
        for i in range(8)
    ]
    barrier = threading.Barrier(len(ids))
    started: list[str] = []

    def start(card_id: str, agent: str) -> None:
        barrier.wait(timeout=10)
        try:
            board.move(card_id, "in_progress", actor=agent)
            started.append(card_id)
        except BoardError:
            pass

    threads = [
        threading.Thread(target=start, args=(card_id, f"a{index}"))
        for index, card_id in enumerate(ids)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(started) == 2 == len(board.cards(status="in_progress"))


# -- creating cards, events, files -----------------------------------------------------


def test_cards_get_sequential_ids_and_a_creation_history(board: BoardStore) -> None:
    first = board.create_card("Architecture", kind="stage", stage="Architecture")
    second = board.create_card(
        "API", kind="work_package", parent_id=first.id, depends_on=[first.id]
    )

    assert (first.id, second.id) == ("K-001", "K-002")
    assert first.history[0].from_status is None and first.history[0].to_status == "backlog"
    assert board.get("k-002").depends_on == ["K-001"]


def test_creating_a_card_validates_its_references(board: BoardStore) -> None:
    with pytest.raises(BoardError, match="No card 'K-404'"):
        board.create_card("Orphan", kind="subtask", parent_id="K-404")
    with pytest.raises(BoardError, match="Unknown card field"):
        board.create_card("Odd", kind="subtask", colour="red")
    with pytest.raises(BoardError, match="starts in backlog or ready"):
        board.create_card("Done already", kind="subtask", status="done")
    with pytest.raises(BoardError, match="must not be empty"):
        board.create_card("   ", kind="subtask")


def test_agents_add_subtasks_only_under_their_own_cards(board: BoardStore) -> None:
    parent = board.create_card(
        "API", kind="work_package", assignee="backend", stage="Build", lane=2
    )

    sub = board.create_subtask(parent.id, "Add pagination", actor="backend", description="d")

    assert (sub.kind, sub.status, sub.assignee, sub.parent_id) == (
        "subtask",
        "ready",
        "backend",
        parent.id,
    )
    assert (sub.stage, sub.lane) == ("Build", 2)
    with pytest.raises(BoardError, match="not yours"):
        board.create_subtask(parent.id, "Sneaky", actor="frontend")


def test_the_board_is_capped(board: BoardStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("engineering_team.board.store.MAX_CARDS", 2)
    board.create_card("a", kind="subtask")
    board.create_card("b", kind="subtask")

    with pytest.raises(BoardError, match="board is full"):
        board.create_card("c", kind="subtask")


def test_every_mutation_emits_a_board_event(board: BoardStore, sink: Recorder) -> None:
    card = board.create_card("API", kind="work_package", assignee="dev", stage="Build", lane=1)
    board.move(card.id, "ready", actor=CONTROLLER)
    board.move(card.id, "in_progress", actor="dev")
    board.comment(card.id, "Starting with the models", author="dev")
    board.report_progress(card.id, "models done", actor="dev")
    board.update(card.id, tokens=1200, cost_usd=0.02)
    board.pause()
    board.unpause()

    assert [name for name, _ in sink.events] == [
        "board.card_created",
        "board.card_moved",
        "board.card_moved",
        "board.card_commented",
        "board.card_updated",
        "board.card_updated",
        "board.paused",
        "board.unpaused",
    ]
    moved = sink.of("board.card_moved")[1]
    assert moved["card_id"] == card.id
    assert (moved["from_status"], moved["to_status"], moved["actor"]) == (
        "ready",
        "in_progress",
        "dev",
    )
    assert (moved["agent"], moved["lane"], moved["stage"]) == ("dev", 1, "Build")
    assert sink.of("board.card_commented")[0]["text"] == "Starting with the models"


def test_a_refused_move_changes_nothing_and_emits_nothing(
    board: BoardStore, sink: Recorder
) -> None:
    card_id = _card_in(board, "in_progress")
    seen = len(sink.events)

    with pytest.raises(BoardError):
        board.move(card_id, "done", actor="dev")

    assert len(sink.events) == seen and board.get(card_id).history[-1].to_status == "in_progress"


def test_board_json_alone_explains_the_run(tmp_path: Path, clock: Clock) -> None:
    board = BoardStore(tmp_path, run_id="run-1", max_in_progress=2, clock=clock, render_delay=0)
    card = board.create_card("API", kind="work_package", assignee="dev", status="ready")
    for step, actor in (
        ("in_progress", "dev"),
        ("blocked", "dev"),
        ("in_progress", "dev"),
        ("verifying", "dev"),
    ):
        clock.advance(minutes=1)
        board.move(card.id, step, actor=actor, reason="waiting on the schema")  # type: ignore[arg-type]
    clock.advance(minutes=1)
    board.move(card.id, "in_progress", actor=CONTROLLER, reason="tests failed", evidence=["tests"])
    board.move(card.id, "verifying", actor="dev")
    board.move(card.id, "done", actor=CONTROLLER, evidence=["tests"])

    saved = json.loads((tmp_path / "board.json").read_text(encoding="utf-8"))
    (record,) = saved["cards"]
    assert [(m["actor"], m["from_status"], m["to_status"]) for m in record["history"]] == [
        (CONTROLLER, None, "ready"),
        ("dev", "ready", "in_progress"),
        ("dev", "in_progress", "blocked"),
        ("dev", "blocked", "in_progress"),
        ("dev", "in_progress", "verifying"),
        (CONTROLLER, "verifying", "in_progress"),
        ("dev", "in_progress", "verifying"),
        (CONTROLLER, "verifying", "done"),
    ]
    assert record["attempts"] == 2 and record["evidence"] == ["tests"]
    assert saved["schema_version"] == 1 and saved["run_id"] == "run-1"


def test_a_board_reloads_from_its_file_and_keeps_numbering(tmp_path: Path) -> None:
    first = BoardStore(tmp_path, run_id="run-1", render_delay=0)
    first.create_card("One", kind="subtask")
    first.pause()

    second = BoardStore(tmp_path, run_id="run-1", render_delay=0)

    assert [card.id for card in second.cards()] == ["K-001"]
    assert second.paused
    assert second.create_card("Two", kind="subtask").id == "K-002"


def test_an_unreadable_board_file_starts_an_empty_board(tmp_path: Path) -> None:
    (tmp_path / "board.json").write_text("{not json", encoding="utf-8")

    assert BoardStore(tmp_path, render_delay=0).cards() == []


def test_returned_cards_are_copies(board: BoardStore) -> None:
    card = board.create_card("One", kind="subtask")
    card.title = "changed"

    assert board.get(card.id).title == "One"


def test_unreadable_run_directories_do_not_break_a_mutation(tmp_path: Path) -> None:
    board = BoardStore(tmp_path / "missing" / "deeper", render_delay=0)

    assert board.create_card("Works", kind="subtask").id == "K-001"  # the directory is created


def test_the_controller_updates_fields_but_not_status(board: BoardStore) -> None:
    card = board.create_card("API", kind="work_package")

    updated = board.update(card.id, assignee="backend", artifacts=["src/api.py"], tokens=5)
    assert (updated.assignee, updated.artifacts, updated.tokens) == ("backend", ["src/api.py"], 5)
    with pytest.raises(BoardError, match="Cannot update status"):
        board.update(card.id, status="done")


# -- progress --------------------------------------------------------------------------


def test_progress_is_weighted_by_card_kind(board: BoardStore, clock: Clock) -> None:
    stage = board.create_card("Build", kind="stage", stage="Build", status="ready")
    package = board.create_card("API", kind="work_package", stage="Build", status="ready")
    check = board.create_card("tests", kind="check", stage="Build", status="ready")
    sub = board.create_card("parser", kind="subtask", stage="Build", status="ready")
    board.create_card("repair", kind="repair", stage="Build", status="ready")  # weight 0
    assert board.progress().overall_percent == 0.0

    for card_id in (package.id, sub.id):
        board.move(card_id, "in_progress", actor=CONTROLLER)
        board.move(card_id, "done", actor=CONTROLLER, evidence=["x"])

    progress = board.progress()
    # done weight 2 + 0.5 over a total of 1 + 2 + 1 + 0.5
    assert progress.overall_percent == round(100 * 2.5 / 4.5, 1)
    assert progress.by_stage == {"Build": round(100 * 2.5 / 3.5, 1)}  # the stage card is excluded
    assert (progress.cards_done, progress.cards_total) == (2, 5)
    assert progress.by_column["done"] == 2 and progress.by_column["ready"] == 3

    board.move(stage.id, "in_progress", actor=CONTROLLER)
    board.move(stage.id, "done", actor=CONTROLLER, stage_success=True)
    assert board.progress().by_stage == {"Build": 100.0}
    assert check.id  # the check card is still open, but the stage is done


def test_cancelled_cards_leave_the_total_and_failed_ones_stay(board: BoardStore) -> None:
    done = board.create_card("a", kind="work_package", status="ready")
    cancelled = board.create_card("b", kind="work_package", status="ready")
    failed = board.create_card("c", kind="work_package", status="ready")
    board.move(done.id, "in_progress", actor=CONTROLLER)
    board.move(done.id, "done", actor=CONTROLLER, evidence=["x"])
    board.move(cancelled.id, "cancelled", actor=CONTROLLER)
    board.move(failed.id, "failed", actor=CONTROLLER, reason="gave up")

    progress = board.progress()

    assert progress.overall_percent == 50.0
    assert progress.cards_total == 2 and "cancelled" not in progress.by_column


def test_progress_lists_blockers_and_the_oldest_card_in_flight(
    board: BoardStore, clock: Clock
) -> None:
    older = board.create_card("older", kind="work_package", assignee="a", status="ready")
    newer = board.create_card("newer", kind="work_package", assignee="b", status="ready")
    board.move(older.id, "in_progress", actor="a")
    clock.advance(minutes=5)
    board.move(newer.id, "in_progress", actor="b")
    clock.advance(minutes=1)
    board.move(newer.id, "blocked", actor="b", reason="needs a decision")

    progress = board.progress()

    assert [(b.id, b.assignee, b.reason) for b in progress.blocked] == [
        (newer.id, "b", "needs a decision")
    ]
    assert progress.oldest_in_progress is not None
    assert (progress.oldest_in_progress.id, progress.oldest_in_progress.age_seconds) == (
        older.id,
        360.0,
    )


def test_an_empty_board_has_zero_progress(board: BoardStore) -> None:
    progress = board.progress()

    assert progress.overall_percent == 0.0 and progress.oldest_in_progress is None
    assert progress.by_stage == {} and progress.blocked == []


# -- steering and pausing --------------------------------------------------------------


def test_a_user_comment_reaches_the_assignee_once(board: BoardStore, sink: Recorder) -> None:
    card = board.create_card("API", kind="work_package", assignee="backend")
    other = board.create_card("UI", kind="work_package", assignee="frontend")
    board.comment(card.id, "Use pagination, please", author=USER)
    board.comment(card.id, "(an agent's own remark)", author="backend")
    board.comment(other.id, "Make the header blue", author=USER)

    assert board.take_steering("backend") == "User note on K-001: Use pagination, please"
    assert board.take_steering("backend") == ""
    assert board.take_steering("frontend") == "User note on K-002: Make the header blue"
    board.comment(card.id, "And add tests", author=USER)
    assert board.take_steering("backend") == "User note on K-001: And add tests"
    assert sink.of("board.steering_delivered")[0] == {"agent": "backend", "notes": 1}


def test_a_run_level_note_reaches_every_agent_once(board: BoardStore) -> None:
    note = board.add_user_note("Prefer SQLite over Postgres")

    assert note.kind == "user_note" and note.status == "done"
    assert board.take_steering("backend") == "User note: Prefer SQLite over Postgres"
    assert board.take_steering("frontend") == "User note: Prefer SQLite over Postgres"
    assert board.take_steering("backend") == ""
    assert board.progress().cards_total == 0  # a note is not work


def test_delivery_survives_a_reload(tmp_path: Path) -> None:
    first = BoardStore(tmp_path, render_delay=0)
    first.add_user_note("Use tabs")
    assert first.take_steering("dev")

    assert BoardStore(tmp_path, render_delay=0).take_steering("dev") == ""


def test_pausing_blocks_waiters_until_unpaused(board: BoardStore) -> None:
    board.pause()
    assert board.paused
    released = threading.Event()

    def wait() -> None:
        board.wait_while_paused(poll=0.01)
        released.set()

    thread = threading.Thread(target=wait)
    thread.start()
    assert not released.wait(timeout=0.2)
    board.unpause()

    assert released.wait(timeout=5) and not board.paused
    thread.join(timeout=5)


def test_cancelling_releases_a_paused_waiter(board: BoardStore) -> None:
    board.pause()
    cancel = threading.Event()
    released = threading.Event()

    def wait() -> None:
        board.wait_while_paused(cancel, poll=0.01)
        released.set()

    thread = threading.Thread(target=wait)
    thread.start()
    cancel.set()

    assert released.wait(timeout=5)
    thread.join(timeout=5)


def test_waiting_on_an_unpaused_board_returns_at_once(board: BoardStore) -> None:
    board.wait_while_paused()  # would hang if it blocked


# -- board.md --------------------------------------------------------------------------


def _showcase(board: BoardStore, clock: Clock) -> None:
    architecture = board.create_card("Architecture", kind="stage", stage="Architecture")
    board.move(architecture.id, "ready", actor=CONTROLLER)
    board.move(architecture.id, "in_progress", actor=CONTROLLER)
    board.move(architecture.id, "done", actor=CONTROLLER, stage_success=True)
    api = board.create_card(
        "API | models", kind="work_package", assignee="backend", stage="Build", status="ready"
    )
    ui = board.create_card("UI", kind="work_package", assignee="frontend", stage="Build")
    board.create_card("Docs", kind="work_package", assignee="docs", stage="Build", status="ready")
    board.move(api.id, "in_progress", actor="backend")
    clock.advance(minutes=12, seconds=30)
    board.report_progress(api.id, "routes\nwritten", actor="backend")
    board.move(ui.id, "blocked", actor="frontend", reason="waiting for the API shape")
    board.add_user_note("Prefer SQLite")


def test_board_markdown_matches_the_golden_file(board: BoardStore, clock: Clock) -> None:
    _showcase(board, clock)

    assert board.markdown() == GOLDEN.read_text(encoding="utf-8")


def test_board_markdown_is_written_on_every_change_and_on_flush(
    tmp_path: Path, clock: Clock
) -> None:
    immediate = BoardStore(tmp_path / "a", clock=clock, render_delay=0)
    immediate.create_card("One", kind="subtask")
    assert "One" in (tmp_path / "a" / "board.md").read_text(encoding="utf-8")

    debounced = BoardStore(tmp_path / "b", clock=clock, render_delay=60)
    debounced.create_card("Two", kind="subtask")
    assert not (tmp_path / "b" / "board.md").exists()  # the timer has not fired
    assert (tmp_path / "b" / "board.json").exists()  # but board.json is always current
    debounced.flush()
    assert "Two" in (tmp_path / "b" / "board.md").read_text(encoding="utf-8")


def test_the_debounced_render_fires_by_itself(tmp_path: Path) -> None:
    board = BoardStore(tmp_path, render_delay=0.05)
    board.create_card("Soon", kind="subtask")
    path = tmp_path / "board.md"

    for _ in range(100):
        if path.exists():
            break
        threading.Event().wait(0.05)

    assert "Soon" in path.read_text(encoding="utf-8")


# -- the run context and the run's end -------------------------------------------------


def test_the_run_context_owns_a_board_sized_by_max_parallel_agents(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context(settings=load_settings(overrides={"parallel.max_parallel_agents": 1}))
    first = ctx.board.create_card("a", kind="work_package", status="ready")
    second = ctx.board.create_card("b", kind="work_package", status="ready")
    ctx.board.move(first.id, "in_progress", actor=CONTROLLER)

    with pytest.raises(BoardError, match="WIP limit reached"):
        ctx.board.move(second.id, "in_progress", actor=CONTROLLER)
    assert ctx.board.path == ctx.run_dir / "board.json"
    events = [e.type for e in read_events(ctx.run_dir / "events.jsonl")]
    assert events.count("board.card_created") == 2


def test_a_finished_run_leaves_board_json_and_board_md(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context()
    recorder = RunRecorder.begin(ctx, request="Build it.")

    with recorder.running():
        ctx.board.create_card("Architecture", kind="stage", stage="Architecture", status="ready")

    assert json.loads((ctx.run_dir / "board.json").read_text(encoding="utf-8"))["cards"]
    assert "Architecture" in (ctx.run_dir / "board.md").read_text(encoding="utf-8")


def test_even_an_empty_board_is_recorded_at_the_end_of_a_run(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context()

    with RunRecorder.begin(ctx).running():
        pass

    assert (ctx.run_dir / "board.json").is_file() and (ctx.run_dir / "board.md").is_file()
