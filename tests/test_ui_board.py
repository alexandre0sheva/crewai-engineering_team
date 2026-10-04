"""The board over HTTP: now and replayed, cards and their trail, teammates, steering, answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from ui_helpers import (
    API,
    make_client,
    sse_events,
    start,
    start_running,
    wait_for,
    wait_status,
)  # noqa: E402

from engineering_team.board.models import BoardState  # noqa: E402
from engineering_team.runtime.inbox import pending_commands  # noqa: E402
from engineering_team.runtime.run_store import RunStore  # noqa: E402


def run_dir_of(run_id: str) -> Path:
    return RunStore(Path.cwd() / "ws" / "demo").run_dir(run_id)


@pytest.fixture
def finished():  # noqa: ANN201
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    return client, run_id


def test_the_board_is_the_final_board_with_progress(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    data = client.get(f"{API}/runs/{run_id}/board").json()

    assert data["run_id"] == run_id and data["paused"] is False
    assert [(c["id"], c["status"], c["assignee"]) for c in data["cards"]] == [
        ("K-001", "done", "backend")
    ]
    assert data["progress"]["overall_percent"] == 100.0 and data["seq"] > 0
    assert [m["to_status"] for m in data["cards"][0]["history"]] == [
        "ready", "in_progress", "verifying", "done",
    ]  # fmt: skip


def test_board_events_carry_the_whole_card(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&types=board.").text)
    moved = [f["data"] for f in frames if f["data"]["type"] == "board.card_moved"]

    assert moved and all(m["data"]["card"]["id"] == "K-001" for m in moved)
    assert [m["data"]["card"]["status"] for m in moved][-1] == "done"
    assert moved[0]["data"]["card"]["history"][-1]["to_status"] == "in_progress"


def test_replay_at_the_last_event_is_the_final_board(finished) -> None:  # noqa: ANN001
    client, run_id = finished
    final = BoardState.model_validate_json((run_dir_of(run_id) / "board.json").read_text())
    last = client.get(f"{API}/runs/{run_id}/board").json()["seq"]

    replay = client.get(f"{API}/runs/{run_id}/board?at={last}").json()

    assert [c["id"] for c in replay["cards"]] == [c.id for c in final.cards]
    for ours, theirs in zip(replay["cards"], final.cards, strict=True):
        assert ours == json.loads(theirs.model_dump_json())  # byte for byte what the board holds


def test_replay_steps_through_the_card_lifecycle(finished) -> None:  # noqa: ANN001
    client, run_id = finished
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
    moves = [(int(f["id"]), f["data"]["data"]["to_status"]) for f in frames
             if f["data"]["type"] == "board.card_moved"]  # fmt: skip

    states = []
    for seq, expected in moves:
        board = client.get(f"{API}/runs/{run_id}/board?at={seq}").json()
        states.append(board["cards"][0]["status"])
        assert states[-1] == expected and board["seq"] == seq
    assert states == ["in_progress", "verifying", "done"]
    before = client.get(f"{API}/runs/{run_id}/board?at=0").json()
    assert before["cards"] == [] and before["seq"] == 0 and before["at"] is None


def test_replay_is_deterministic_and_clamps_to_the_log(finished) -> None:  # noqa: ANN001
    client, run_id = finished
    last = client.get(f"{API}/runs/{run_id}/board").json()["seq"]

    first = client.get(f"{API}/runs/{run_id}/board?at=7").text
    second = client.get(f"{API}/runs/{run_id}/board?at=7").text
    beyond = client.get(f"{API}/runs/{run_id}/board?at={last + 1000}").json()

    assert first == second
    assert beyond["seq"] <= last and beyond["progress"]["overall_percent"] == 100.0
    assert client.get(f"{API}/runs/{run_id}/board?at=-1").status_code == 422
    assert client.get(f"{API}/runs/{run_id}/board?at=abc").status_code == 422


def test_replay_progress_is_measured_at_the_event_not_now(finished) -> None:  # noqa: ANN001
    client, run_id = finished
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&types=board.").text)
    started = next(f for f in frames if f["data"]["data"].get("to_status") == "in_progress")

    one = client.get(f"{API}/runs/{run_id}/board?at={started['id']}").json()

    assert one["at"] == started["data"]["ts"]
    assert one["progress"]["oldest_in_progress"]["age_seconds"] == 0.0


def test_a_card_has_detail_and_a_tool_call_trail(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    detail = client.get(f"{API}/runs/{run_id}/cards/k-001").json()

    assert detail["card"]["id"] == "K-001" and detail["card"]["title"] == "Build the thing"
    assert len(detail["trail"]) == 8
    assert {t["tool"] for t in detail["trail"]} == {"Write File"}
    assert all(t["ok"] for t in detail["trail"]) and "path" in detail["trail"][0]["args"]
    assert all("card" not in e["data"] for e in detail["events"])  # shown once, not per event
    assert [e["type"] for e in detail["events"]][0] == "board.card_created"
    assert client.get(f"{API}/runs/{run_id}/cards/K-099").status_code == 404


def test_teammates_show_presence_and_counters(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    agents = client.get(f"{API}/runs/{run_id}/agents").json()

    assert [a["agent"] for a in agents] == ["backend"]
    backend = agents[0]
    assert backend["state"] == "idle"  # the run is over
    assert backend["tool_calls"] == 8 and backend["failed_calls"] == 0
    assert backend["last_tool"] == "Write File" and backend["last_tool_ok"] is True
    assert backend["cards"] == ["K-001"] and backend["card_id"] is None


def test_a_working_teammate_is_working_on_its_card() -> None:
    client, _ = make_client()
    run_id = start_running(client, "SLOW")
    agent = wait_for(
        lambda: next(
            (a for a in client.get(f"{API}/runs/{run_id}/agents").json() if a["tool_calls"] > 0),
            None,
        ),
        "the teammate's first tool call",
    )

    assert agent["state"] == "working" and agent["card_id"] == "K-001"
    assert agent["card_title"] == "Build the thing"
    client.post(f"{API}/runs/{run_id}/cancel")
    wait_status(client, run_id, "cancelled")


# -- steering --------------------------------------------------------------------------------


def test_a_note_to_a_card_reaches_the_running_process_through_the_inbox() -> None:
    client, _ = make_client()
    run_id = start_running(client, "SLOW")
    wait_for(lambda: client.get(f"{API}/runs/{run_id}/board").json()["cards"], "the card")

    queued = client.post(f"{API}/runs/{run_id}/cards/k-001/comments", json={"text": "Use SQLite."})

    assert queued.status_code == 202 and queued.json()["card"] == "K-001"
    comments = wait_for(
        lambda: client.get(f"{API}/runs/{run_id}/cards/K-001").json()["card"]["comments"],
        "the process to apply the note",
    )
    assert comments[0]["author"] == "user" and comments[0]["text"] == "Use SQLite."
    assert pending_commands(run_dir_of(run_id)) == []  # consumed
    client.post(f"{API}/runs/{run_id}/cancel")
    wait_status(client, run_id, "cancelled")


def test_a_run_level_note_becomes_a_user_note_card() -> None:
    client, _ = make_client()
    run_id = start_running(client, "SLOW")
    wait_for(lambda: client.get(f"{API}/runs/{run_id}/board").json()["cards"], "the card")

    assert (
        client.post(f"{API}/runs/{run_id}/notes", json={"text": "Skip the export."}).status_code
        == 202
    )

    cards = wait_for(
        lambda: [c for c in client.get(f"{API}/runs/{run_id}/board").json()["cards"]
                 if c["kind"] == "user_note"],
        "the note card",
    )  # fmt: skip
    assert cards[0]["comments"][0]["text"] == "Skip the export."
    client.post(f"{API}/runs/{run_id}/cancel")
    wait_status(client, run_id, "cancelled")


def test_pause_and_unpause_are_visible_on_the_board() -> None:
    client, _ = make_client()
    run_id = start_running(client, "SLOW")
    wait_for(lambda: client.get(f"{API}/runs/{run_id}/board").json()["cards"], "the card")

    assert client.post(f"{API}/runs/{run_id}/pause").status_code == 202
    wait_for(lambda: client.get(f"{API}/runs/{run_id}/board").json()["paused"], "the pause")
    assert client.get(f"{API}/runs/{run_id}").json()["paused"] is True
    assert client.post(f"{API}/runs/{run_id}/unpause").status_code == 202
    wait_for(lambda: not client.get(f"{API}/runs/{run_id}/board").json()["paused"], "the unpause")
    client.post(f"{API}/runs/{run_id}/cancel")
    wait_status(client, run_id, "cancelled")


def test_a_command_for_a_run_nobody_is_working_on_waits_in_its_inbox(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    assert client.post(f"{API}/runs/{run_id}/notes", json={"text": "Later."}).status_code == 202

    assert len(pending_commands(run_dir_of(run_id))) == 1  # applied when the run is resumed


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("notes", {"text": ""}),
        ("notes", {"text": "x" * 5000}),
        ("notes", {}),
        ("cards/K-001/comments", {"text": "  "}),
        ("answer", {"question_id": ""}),
    ],
)
def test_bad_steering_is_a_422(finished, path: str, body: dict[str, str]) -> None:  # noqa: ANN001
    client, run_id = finished

    response = client.post(f"{API}/runs/{run_id}/{path}", json=body)

    assert response.status_code == 422 or (path == "notes" and body.get("text") == "  ")
    assert pending_commands(run_dir_of(run_id)) == []


def test_a_comment_on_an_unknown_card_is_a_404(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    response = client.post(f"{API}/runs/{run_id}/cards/K-042/comments", json={"text": "hi"})

    assert response.status_code == 404 and pending_commands(run_dir_of(run_id)) == []


# -- the team's questions ------------------------------------------------------------------------


def test_a_question_is_listed_answered_through_the_api_and_the_run_goes_on() -> None:
    client, _ = make_client()
    run_id = start_running(client, "ASK")
    questions = wait_for(lambda: client.get(f"{API}/runs/{run_id}/questions").json(), "a question")

    assert questions[0]["text"] == "Which database?" and questions[0]["agent"] == "backend"
    assert client.get(f"{API}/runs/{run_id}").json()["questions"][0]["id"] == questions[0]["id"]
    waiting = client.get(f"{API}/runs/{run_id}/agents").json()[0]
    assert waiting["state"] == "waiting" and waiting["waiting_for"] == questions[0]["id"]

    response = client.post(
        f"{API}/runs/{run_id}/answer", json={"question_id": questions[0]["id"], "text": "SQLite"}
    )

    assert response.status_code == 202 and response.json()["declined"] is False
    wait_status(client, run_id, "succeeded")
    kinds = [f["data"]["type"] for f in
             sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)]  # fmt: skip
    assert "question.answered" in kinds
    written = [f for f in sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
               if f["data"]["type"] == "note.written"]  # fmt: skip
    assert written[0]["data"]["data"]["answer"] == "SQLite"  # the asker got the answer
    assert client.get(f"{API}/runs/{run_id}/questions").json() == []


def test_the_replay_has_the_teammate_waiting_exactly_while_the_question_is_open() -> None:
    client, _ = make_client()
    run_id = start_running(client, "ASK")  # one lane: its answer is the very next event
    question = wait_for(lambda: client.get(f"{API}/runs/{run_id}/questions").json(), "a question")[
        0
    ]
    client.post(f"{API}/runs/{run_id}/answer", json={"question_id": question["id"], "text": "x"})
    wait_status(client, run_id, "succeeded")
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&types=question").text)
    seqs = {f["data"]["type"]: f["data"]["seq"] for f in frames}
    assert seqs["question.answered"] == seqs["question"] + 1

    asked = client.get(f"{API}/runs/{run_id}/agents?at={seqs['question']}").json()
    assert [(a["agent"], a["state"], a["waiting_for"]) for a in asked] == [
        ("backend", "waiting", question["id"])
    ]
    answered = client.get(f"{API}/runs/{run_id}/agents?at={seqs['question.answered']}").json()
    assert [(a["state"], a["waiting_for"]) for a in answered] == [("working", None)]


def test_a_question_can_be_declined_with_an_empty_answer() -> None:
    client, _ = make_client()
    run_id = start_running(client, "ASK")
    question = wait_for(lambda: client.get(f"{API}/runs/{run_id}/questions").json(), "a question")[
        0
    ]

    response = client.post(f"{API}/runs/{run_id}/answer", json={"question_id": question["id"]})

    assert response.json()["declined"] is True
    wait_status(client, run_id, "succeeded")
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
    written = next(f for f in frames if f["data"]["type"] == "note.written")
    assert written["data"]["data"]["answer"] is None  # the team assumes


def test_answering_a_question_that_is_not_open_is_a_409(finished) -> None:  # noqa: ANN001
    client, run_id = finished

    response = client.post(
        f"{API}/runs/{run_id}/answer", json={"question_id": "Q-001", "text": "x"}
    )

    assert response.status_code == 409 and "No open question" in response.json()["error"]
    assert pending_commands(run_dir_of(run_id)) == []


def test_a_run_started_without_interaction_does_not_wait_for_answers() -> None:
    client, _ = make_client()
    run_id = start(client, "ASK", interactive=False)

    wait_status(client, run_id, "succeeded")  # the question went unanswered at once
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
    assert any(f["data"]["type"] == "question.unanswered" for f in frames)
    assert client.get(f"{API}/runs/{run_id}/questions").json() == []
