"""``ui --demo`` through the one entry point: a request that escalates, a question answered with
checked sources, best-of-N attempts, a follow-up, and the views the results page shows for each."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from git_helpers import require_git  # noqa: E402
from ui_helpers import API, make_demo_client, wait_status  # noqa: E402

AUTO = {"strategy": "auto"}


@pytest.fixture
def demo() -> tuple[TestClient, Path]:
    require_git()
    return make_demo_client()


def run_do(client: TestClient, request: str, **fields: object) -> dict:  # type: ignore[type-arg]
    started = client.post(
        f"{API}/runs", json={"mode": "do", "request": request, "interactive": False, **fields}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    wait_status(client, run_id, "succeeded", "failed", timeout=180)
    return client.get(f"{API}/runs/{run_id}/results").json()  # type: ignore[no-any-return]


@pytest.mark.git
def test_a_change_request_escalates_up_the_ladder_and_the_page_gets_the_timeline(demo) -> None:  # noqa: ANN001
    client, repo = demo

    results = run_do(client, "Add a notes search command.", repo=str(repo), options=AUTO)

    assert results["status"] == "succeeded" and results["verdict"] == "verified"
    escalation = results["views"]["escalation"]
    assert escalation["climbed"] is True
    assert [r["rung"] for r in escalation["rungs"]] == ["solo", "team", "pipeline"]
    assert [r["status"] for r in escalation["rungs"]] == ["escalated", "escalated", "succeeded"]
    assert escalation["headline"] == "Escalated: solo → team → pipeline"
    assert escalation["rungs"][0]["failing_checks"], "the weak rung's failing check is named"
    assert escalation["rungs"][0]["why"] and escalation["rungs"][2]["cost"].startswith("$")
    assert results["views"]["answer"] is None and results["views"]["attempts"] is None


@pytest.mark.git
def test_a_question_is_answered_with_sources_the_controller_checked(demo) -> None:  # noqa: ANN001
    client, repo = demo

    results = run_do(client, "How does the main function work?", repo=str(repo), options=AUTO)

    answer = results["views"]["answer"]
    assert answer["kind"] == "answer" and answer["tone"] == "good"
    assert answer["headline"] == "2 of 2 citation(s) verified by the controller"
    assert [c["target"] for c in answer["citations"]] == ["app/main.py:1-2", "README.md:1"]
    assert all(c["ok"] and c["status"] == "verified" for c in answer["citations"])
    parts = answer["sections"][0]["parts"]  # ``[1]`` in the body is a link to source 1
    assert {"cite": 1} in parts and any("text" in p for p in parts)
    assert answer["open_questions"]
    assert results["views"]["escalation"] is None


@pytest.mark.git
def test_best_of_n_attempts_are_shown_side_by_side_with_how_one_was_chosen(demo) -> None:  # noqa: ANN001
    client, _ = demo

    results = run_do(
        client,
        "Build a CLI that stores and lists notes. AC-1 add. AC-2 list.",
        options={**AUTO, "attempts": 2},
    )

    attempts = results["views"]["attempts"]
    assert attempts["count"] == 2 and attempts["chosen"] == "a1"
    assert attempts["method"] == "fallback" and "smallest passing diff" in attempts["method_text"]
    assert [a["label"] for a in attempts["attempts"]] == ["a1", "a2"]
    assert [a["chosen"] for a in attempts["attempts"]] == [True, False]
    assert all(a["status"] == "passed" and a["checks"] == "1 of 1" for a in attempts["attempts"])
    assert attempts["attempts"][0]["diff_lines"] < attempts["attempts"][1]["diff_lines"]
    assert attempts["ledger"].startswith("Ledger: 2 attempt(s)")
    assert attempts["judge"]["error"]  # the demo calls no model: the judge fell back, and says so


@pytest.mark.git
def test_a_follow_up_run_joins_the_thread_of_the_run_it_follows(demo) -> None:  # noqa: ANN001
    client, repo = demo
    first = run_do(client, "How does the main function work?", repo=str(repo), options=AUTO)
    parent = first["run_id"]
    assert first["views"]["thread"]["can_continue"] is True
    assert first["views"]["thread"]["in_thread"] is False

    started = client.post(
        f"{API}/runs",
        json={
            "mode": "continue",
            "parent": parent,
            "request": "Add a notes search command.",
            "options": AUTO,
            "interactive": False,
        },
    )

    assert started.status_code == 202, started.text
    child = started.json()["run_id"]
    wait_status(client, child, "succeeded", "failed", timeout=180)
    thread = client.get(f"{API}/runs/{child}/results").json()["views"]["thread"]
    assert thread["in_thread"] is True
    assert [r["run_id"] for r in thread["runs"]] == [parent, child]
    assert [r["current"] for r in thread["runs"]] == [False, True]
    assert thread["runs"][1]["parent"] == parent


def test_the_demos_lesson_library_has_one_approved_lesson_and_two_waiting_and_is_made_once(
    tmp_path: Path,
) -> None:
    from engineering_team.learning.library import LessonLibrary
    from engineering_team.ui.demo_data import seed_learning

    seed_learning(tmp_path)
    seed_learning(tmp_path)  # a second start of the demo adds nothing

    library = LessonLibrary(tmp_path)
    approved, _ = library.load()
    waiting, _ = library.load(pending=True)
    assert len(approved) == 1 and len(waiting) == 2
    assert {lesson.domain for lesson in [*approved, *waiting]} == {"feature/python"}


@pytest.mark.git
def test_best_of_n_attempts_on_the_sample_repository_pass_and_leave_a_patch(demo) -> None:  # noqa: ANN001
    client, repo = demo

    results = run_do(
        client, "Add a notes search command.", repo=str(repo), options={**AUTO, "attempts": 2}
    )

    assert results["status"] == "succeeded" and results["verdict"] == "verified"
    assert results["views"]["attempts"]["chosen"] == "a1"
    assert results["diff"]["files"], "the chosen attempt's change is the run's diff"
    assert results["merge"]["branch"]
