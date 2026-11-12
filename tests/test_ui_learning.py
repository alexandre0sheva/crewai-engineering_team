"""The learning screens' API: a run's harness and lessons, repository memory with forget, and the
experience library with approve and reject. Everything is read and worded by the server."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from ui_helpers import API, make_client, start, wait_status  # noqa: E402

from engineering_team.contracts import utc_now  # noqa: E402
from engineering_team.learning.lessons import Lesson, LessonEvidence, lesson_id  # noqa: E402
from engineering_team.learning.library import LessonLibrary, user_library  # noqa: E402
from engineering_team.learning.retrieve import LESSONS_FILE  # noqa: E402
from engineering_team.memory import Evidence, Fact, MemoryStore  # noqa: E402
from engineering_team.memory.facts import fact_id  # noqa: E402

DOMAIN = "bugfix/python"


def lesson(cue: str, *, age: int = 0) -> Lesson:
    text = f"Add to stages/fix: think about {cue}."
    return Lesson(
        id=lesson_id(DOMAIN, cue, text),
        domain=DOMAIN,
        cue=cue,
        lesson=text,
        component="stages/fix",
        confidence="medium",
        evidence=LessonEvidence(run_id="r1", verdict="verified", check_ids=["repro"]),
        created=utc_now() - timedelta(minutes=age),
    )


def user_lessons() -> LessonLibrary:
    root = user_library()
    assert root is not None
    return LessonLibrary(root)


def fact(topic: str, text: str, *, status: str = "verified") -> Fact:
    return Fact(
        id=fact_id("command", topic),
        kind="command",
        topic=topic,
        text=text,
        status=status,  # type: ignore[arg-type]
        evidence=Evidence(run_id="20260101-000000-aaaaaa", check_ids=["tests"]),
    )


# -- repository memory -----------------------------------------------------------------------


def test_the_memory_of_a_project_lists_its_facts_and_forgets_one_on_request(tmp_path: Path) -> None:
    client, _ = make_client()
    store = MemoryStore(tmp_path)
    keep = store.upsert(fact("tests", "Run the tests with pytest -q."))
    drop = store.upsert(fact("lint", "Lint with ruff check .", status="proposed"))

    listed = client.get(f"{API}/memory", params={"repo": str(tmp_path)}).json()

    assert {f["id"] for f in listed["facts"]} == {keep.id, drop.id}
    assert listed["counts"] == {"verified": 1, "proposed": 1, "stale": 0}
    first = listed["facts"][0]
    assert first["status"] == "verified" and first["run_id"] == "20260101-000000-aaaaaa"
    assert first["check_ids"] == ["tests"]

    gone = client.post(f"{API}/memory/{drop.id}/forget", json={"repo": str(tmp_path)})

    assert gone.status_code == 200 and gone.json() == {"forgotten": drop.id}
    after = client.get(f"{API}/memory", params={"repo": str(tmp_path)}).json()
    assert [f["id"] for f in after["facts"]] == [keep.id]


def test_a_project_without_memory_has_an_empty_list_and_nothing_is_written(tmp_path: Path) -> None:
    client, _ = make_client()

    listed = client.get(f"{API}/memory", params={"repo": str(tmp_path)}).json()

    assert listed["facts"] == [] and listed["problems"] == []
    assert not (tmp_path / ".engineering-team").exists()


def test_forgetting_needs_an_existing_fact_in_an_existing_project(tmp_path: Path) -> None:
    client, _ = make_client()

    assert client.post(f"{API}/memory/nope/forget", json={"repo": str(tmp_path)}).status_code == 404
    assert client.post(f"{API}/memory/nope/forget", json={}).status_code == 422
    assert client.get(f"{API}/memory", params={"repo": "relative"}).status_code == 422


def test_a_damaged_memory_file_is_reported_not_raised(tmp_path: Path) -> None:
    client, _ = make_client()
    store = MemoryStore(tmp_path)
    store.directory.mkdir(parents=True)
    store.path.write_text("this is not json\n", encoding="utf-8")

    listed = client.get(f"{API}/memory", params={"repo": str(tmp_path)})

    assert listed.status_code == 200
    assert listed.json()["facts"] == [] and "not a valid fact" in listed.json()["problems"][0]


# -- lessons -------------------------------------------------------------------------------------


def test_pending_lessons_are_listed_first_and_can_be_approved_or_rejected() -> None:
    client, _ = make_client()
    library = user_lessons()
    approved = lesson("a failing test names the wrong module", age=5)
    waiting = lesson("the traceback ends in a missing import")
    refused = lesson("the fix touches generated files")
    library.add(approved)
    library.add(waiting, pending=True)
    library.add(refused, pending=True)

    listed = client.get(f"{API}/lessons").json()

    assert listed["libraries"][0]["name"] == "user" and listed["pending"] == 2
    assert [r["pending"] for r in listed["lessons"]] == [True, True, False]
    row = next(r for r in listed["lessons"] if r["id"] == waiting.id)
    assert row["cue"] == waiting.cue and row["component"] == "stages/fix"
    assert row["evidence"]["run_id"] == "r1" and row["evidence"]["check_ids"] == ["repro"]

    ok = client.post(f"{API}/lessons/{waiting.id}/approve", json={})
    no = client.post(f"{API}/lessons/{refused.id}/reject", json={})

    assert ok.json() == {"approved": waiting.id, "domain": DOMAIN, "library": "user"}
    assert no.json() == {"removed": refused.id, "library": "user"}
    after = {r["id"]: r["pending"] for r in client.get(f"{API}/lessons").json()["lessons"]}
    assert after == {approved.id: False, waiting.id: False}


def test_approving_what_is_not_pending_or_not_there_is_refused() -> None:
    client, _ = make_client()
    library = user_lessons()
    live = lesson("already approved")
    library.add(live)

    assert client.post(f"{API}/lessons/{live.id}/approve", json={}).status_code == 409
    assert client.post(f"{API}/lessons/zzzzzz/approve", json={}).status_code == 404
    assert client.post(f"{API}/lessons/zzzzzz/reject", json={}).status_code == 404
    assert client.post(f"{API}/lessons/x/approve", json={"library": "other"}).status_code == 422


def test_a_repository_library_is_listed_with_the_project_and_acted_on_in_its_library(
    tmp_path: Path,
) -> None:
    client, _ = make_client()
    repo_library = LessonLibrary(tmp_path / ".engineering-team" / "library", name="repository")
    waiting = lesson("repository specific cue")
    repo_library.add(waiting, pending=True)

    plain = client.get(f"{API}/lessons").json()
    with_repo = client.get(f"{API}/lessons", params={"repo": str(tmp_path)}).json()

    assert plain["lessons"] == []
    assert [r["library"] for r in with_repo["lessons"]] == ["repository"]
    done = client.post(
        f"{API}/lessons/{waiting.id}/approve", json={"repo": str(tmp_path), "library": "repository"}
    )
    assert done.json()["library"] == "repository"


# -- a run's harness and lessons -----------------------------------------------------------------


def test_a_runs_learning_view_says_which_harness_and_lessons_it_had() -> None:
    client, _ = make_client()
    run_id = start(client, "Fix it.", mode="do")
    wait_status(client, run_id, "succeeded")
    found = client.get(f"{API}/runs/{run_id}").json()
    run_dir = Path(found["workspace"]) / ".engineering-team" / "runs" / run_id
    given = lesson("the run's own cue")
    user_lessons().add(given)
    (run_dir / LESSONS_FILE).write_text(
        json.dumps({"ids": [given.id, "gone-lesson"], "section": ""}), encoding="utf-8"
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["harness"] = {
        "domain": "bugfix/python",
        "packs": [{"name": "base", "source": "bundled", "sha256": "abc123"}],
        "skills_listed": ["pytest-failures", "no-such-skill"],
        "skills_loaded": ["pytest-failures"],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    view = client.get(f"{API}/runs/{run_id}/learning").json()

    harness = view["harness"]
    assert harness["recorded"] and harness["domain"] == "bugfix/python"
    assert harness["packs"][0]["name"] == "base"
    by_name = {s["name"]: s for s in harness["skills"]}
    assert by_name["pytest-failures"]["loaded"] is True
    assert by_name["pytest-failures"]["description"] and by_name["pytest-failures"]["pack"]
    assert by_name["no-such-skill"]["loaded"] is False
    assert by_name["no-such-skill"]["description"] == "(no longer in the harness)"
    ids = [row["id"] for row in view["lessons"]["given"]]
    assert ids == [given.id, "gone-lesson"]
    assert view["lessons"]["given"][0]["cue"] == given.cue
    assert view["lessons"]["given"][1]["gone"] is True
    assert view["memory"]["repo"] == view["project"]


def test_a_run_from_before_harnesses_were_recorded_says_so() -> None:
    client, _ = make_client()
    run_id = start(client, "Build it.", mode="do")
    wait_status(client, run_id, "succeeded")

    view = client.get(f"{API}/runs/{run_id}/learning").json()

    assert (
        view["harness"]["recorded"] is False and "before runs recorded" in view["harness"]["note"]
    )
    assert view["lessons"]["given"] == []


def test_the_learning_view_of_an_unknown_run_is_a_404() -> None:
    client, _ = make_client()

    assert client.get(f"{API}/runs/20200101-000000-abcdef/learning").status_code == 404
