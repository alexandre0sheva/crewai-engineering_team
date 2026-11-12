"""``POST /triage``: the plan ``do`` would show, computed by the server, before anything starts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from git_helpers import require_git  # noqa: E402
from ui_helpers import API, make_client, make_demo_client  # noqa: E402

from engineering_team.ui.demo import ensure_sample_repo  # noqa: E402

REQUEST = "Add a notes search command."


@pytest.fixture
def sample() -> Path:
    require_git()
    return ensure_sample_repo(Path.cwd() / "home")


def test_a_request_about_a_project_is_previewed_with_its_rung_stages_and_estimate(
    sample: Path,
) -> None:
    client, _ = make_client(strategy="auto")

    response = client.post(f"{API}/triage", json={"request": REQUEST, "repo": str(sample)})

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["understood"]["intents"][0] == "feature"
    assert plan["understood"]["headline"] == "A new feature"
    assert plan["understood"]["by"] == "signals only"
    assert plan["understood"]["project"] == str(sample)
    assert plan["route"]["key"] == "repository"
    assert plan["route"]["text"].startswith("strategy auto, starting on the")
    assert not plan["route"]["text"].endswith(":")
    assert plan["strategy"] == "auto"
    assert plan["rung"]["start"] in ("solo", "team", "pipeline")
    assert plan["rung"]["reasons"]
    assert plan["stages"] and all({"name", "kind", "why"} <= set(s) for s in plan["stages"])
    assert plan["estimate"]["first"].startswith("$")
    assert plan["estimate"]["text"].startswith("Estimated cost")
    assert plan["harness"]["domain"]
    assert plan["model_used"] is False


def test_without_a_project_the_request_is_a_new_project() -> None:
    client, _ = make_client()

    plan = client.post(
        f"{API}/triage", json={"request": "Build a CLI that stores and lists notes."}
    ).json()

    assert plan["understood"]["project"] is None
    assert plan["understood"]["where"] == "a new project"
    assert plan["route"]["key"] == "new"


def test_a_question_is_answered_read_only_and_not_estimated(sample: Path) -> None:
    client, _ = make_client()

    plan = client.post(
        f"{API}/triage", json={"request": "How does the main function work?", "repo": str(sample)}
    ).json()

    assert plan["route"]["key"] == "answer"
    assert plan["estimate"]["read_only"] is True
    assert plan["rung"] is None


def test_the_overrides_change_the_plan(sample: Path) -> None:
    client, _ = make_client(strategy="auto")
    body = {"request": REQUEST, "repo": str(sample)}

    forced = client.post(f"{API}/triage", json={**body, "intent": "fix"}).json()
    pipeline = client.post(f"{API}/triage", json={**body, "strategy": "pipeline"}).json()
    attempts = client.post(f"{API}/triage", json={**body, "attempts": 3}).json()

    assert forced["understood"]["intents"][0] == "bugfix"
    assert pipeline["rung"] is None and pipeline["strategy"] == "pipeline"
    assert attempts["attempts"]["count"] == 3
    assert attempts["route"]["text"].startswith("best of 3 attempts")
    assert not attempts["route"]["text"].endswith(("stages", ":"))


@pytest.mark.parametrize(
    "body",
    [
        {"request": "x", "repo": "relative/path"},
        {"request": "x", "repo": "/no/such/directory"},
        {"request": "x", "intent": "dance"},
        {"request": "x", "provider": "nobody"},
        {"request": "x", "profile": "nothing"},
        {"request": "   "},
        {"request": "x", "attempts": 99},
    ],
)
def test_a_preview_that_cannot_be_made_says_why(body: dict[str, object]) -> None:
    client, _ = make_client()

    response = client.post(f"{API}/triage", json=body)

    assert response.status_code == 422
    assert response.json()["error"]


def test_demo_mode_never_calls_the_model_even_when_asked_to_refine() -> None:
    require_git()
    client, repo = make_demo_client()

    plan = client.post(
        f"{API}/triage", json={"request": REQUEST, "repo": str(repo), "refine": True}
    ).json()

    assert plan["model_used"] is False
    assert any("no model" in note.lower() for note in plan["notes"])


def test_a_post_without_the_page_header_is_refused() -> None:
    client, _ = make_client()
    client.headers.pop("X-Engineering-Team")

    assert client.post(f"{API}/triage", json={"request": "x"}).status_code in (400, 403)


def test_the_start_form_offers_the_kinds_of_work_a_request_may_be_forced_to() -> None:
    client, _ = make_client()

    options = client.get(f"{API}/options").json()

    offered = {i["key"]: i["label"] for i in options["intents"]}
    assert {"new", "feature", "fix", "review", "answer"} <= set(offered)
    assert all(offered.values())
