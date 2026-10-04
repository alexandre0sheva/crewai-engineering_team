"""What the live dashboard reads, from a scripted demo run: every card state, the teammates now
and replayed, what needs attention, the budget, the timeline, and a steering note's delivery."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from git_helpers import require_git  # noqa: E402
from ui_helpers import API, make_demo_client, sse_events, wait_for, wait_status  # noqa: E402

pytestmark = pytest.mark.git


def start_demo(client, repo, **fields):  # noqa: ANN001, ANN201
    body = {"mode": "feature", "repo": str(repo), "request": "Add notes. AC-1 add. AC-2 list."}
    started = client.post(f"{API}/runs", json={**body, **fields})
    assert started.status_code == 202, started.text
    return started.json()["run_id"]


def events_of(client, run_id: str, types: str = "") -> list[dict]:  # noqa: ANN001
    text = client.get(f"{API}/runs/{run_id}/events?follow=false&types={types}").text
    return [frame["data"] for frame in sse_events(text)]


def test_a_demo_run_shows_every_state_the_dashboard_has() -> None:
    require_git()
    client, repo = make_demo_client(pause="0.05")
    run_id = start_demo(client, repo)  # interactive: the team asks a question and waits

    # While it waits: the question, the card it blocked, and the teammate who waits.
    view = wait_for(
        lambda: (v := client.get(f"{API}/runs/{run_id}").json())["questions"] and v,
        "the team's question",
        timeout=90,
    )
    kinds = {item["kind"] for item in view["attention"]}
    assert {"question", "blocked"} <= kinds
    asked = view["questions"][0]
    blocked = client.get(f"{API}/runs/{run_id}/board").json()["cards"]
    assert [c["status"] for c in blocked if c["id"] == asked["card_id"]] == ["blocked"]
    agents = {a["agent"]: a for a in client.get(f"{API}/runs/{run_id}/agents").json()}
    assert agents[asked["agent"]]["state"] == "waiting"
    assert agents[asked["agent"]]["waiting_for"] == asked["id"]
    assert view["budget"]["limits"][0]["name"] == "max_cost_usd"

    answered = client.post(
        f"{API}/runs/{run_id}/answer", json={"question_id": asked["id"], "text": "Markdown"}
    )
    assert answered.status_code == 202
    wait_status(client, run_id, "succeeded", "failed", timeout=120)

    # Afterwards: the board went through every state, a check failed and was repaired, three
    # lanes ran side by side, the budget warned, and the reviewers left findings.
    moves = events_of(client, run_id, "board.card_moved")
    assert [m["seq"] for m in moves] == sorted(m["seq"] for m in moves)  # delivered in order
    seen = {m["data"]["to_status"] for m in moves}
    assert {"ready", "in_progress", "verifying", "blocked", "done", "failed"} <= seen
    final = client.get(f"{API}/runs/{run_id}").json()
    assert final["status"] == "succeeded" and not final["questions"]
    assert final["budget"]["state"] == "warning"
    assert any(i["kind"] == "budget" for i in final["attention"])
    assert [c["status"] for c in final["checks"]] == ["passed"]  # failed first, then repaired
    statuses = [e["data"]["status"] for e in events_of(client, run_id, "check.finished")]
    assert statuses == ["passed", "failed", "passed"]
    timeline = client.get(f"{API}/runs/{run_id}/timeline").json()
    assert len({lane["lane"] for lane in timeline["lanes"] if lane["stage"] == "implement"}) == 3
    assert {f["severity"] for f in timeline["findings"]} >= {"medium", "low"}
    working = [a for a in client.get(f"{API}/runs/{run_id}/agents").json() if a["tool_calls"]]
    assert working and all(a["tokens"] > 0 and a["model"] for a in working)

    # Replay: the same event always gives the same teammates, and the question was open then.
    # (The question's own seq: the next event may be its answer, when the other lanes are idle.)
    seqs = {e["type"]: e["seq"] for e in events_of(client, run_id, "question")}
    at = seqs["question"]
    first = client.get(f"{API}/runs/{run_id}/agents?at={at}").json()
    assert first == client.get(f"{API}/runs/{run_id}/agents?at={at}").json()
    assert any(a["state"] == "waiting" for a in first)
    answered = client.get(f"{API}/runs/{run_id}/agents?at={seqs['question.answered']}").json()
    assert not any(a["state"] == "waiting" for a in answered)  # and not once it is answered
    assert all(a["state"] == "idle" for a in client.get(f"{API}/runs/{run_id}/agents?at=0").json())
    last = events_of(client, run_id)[-1]["seq"]
    clamped = client.get(f"{API}/runs/{run_id}/agents?at={last + 1000}").json()
    assert clamped == client.get(f"{API}/runs/{run_id}/agents?at={last}").json()
    assert client.get(f"{API}/runs/{run_id}/agents?at=-1").status_code == 422


def test_a_steering_note_is_delivered_to_each_teammate_once() -> None:
    require_git()
    client, repo = make_demo_client(pause="0.15")
    run_id = start_demo(client, repo, interactive=False)
    wait_status(client, run_id, "running")

    sent = client.post(f"{API}/runs/{run_id}/notes", json={"text": "Prefer small functions."})
    assert sent.status_code == 202
    wait_status(client, run_id, "succeeded", "failed", timeout=120)

    read = [
        e for e in events_of(client, run_id, "tool.call") if e["data"]["tool"] == "Read Steering"
    ]
    assert read, "no teammate was handed the note"
    assert all("Prefer small functions." in str(e["data"]["args"]) for e in read)
    agents = [e["agent"] for e in read]
    assert len(agents) == len(set(agents))  # delivered once to each, never repeated
    delivered = events_of(client, run_id, "board.steering_delivered")
    assert sorted(e["agent"] for e in delivered) == sorted(agents)
