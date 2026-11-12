"""Starting ``do`` and ``continue`` from the API: the new-run page's one textbox and the
follow-up box. Runs are the stand-in CLI of ``tests/stub_run.py``."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from ui_helpers import API, make_client, start, wait_status  # noqa: E402


def argv_of(launcher, run_id: str) -> list[str]:  # noqa: ANN001
    record = launcher.record(run_id)
    assert record is not None
    return record.argv


def test_do_starts_the_entry_command_without_asking_and_with_the_request_as_a_file() -> None:
    client, launcher = make_client()

    run_id = start(client, "Add a search command.", mode="do")

    argv = argv_of(launcher, run_id)
    tail = argv[argv.index("do") :]
    assert tail[:3] == ["do", "--yes", "--non-interactive"]
    assert "--request-file" in tail
    assert "--repo" not in tail and not any(a.startswith("--mode") for a in tail)
    assert "--answers-via-inbox" in argv[: argv.index("do")]  # globals come first
    wait_status(client, run_id, "succeeded")


def test_do_carries_the_project_the_forced_intent_and_the_overrides(tmp_path) -> None:  # noqa: ANN001
    client, launcher = make_client()
    project = tmp_path / "shop"
    project.mkdir()

    run_id = start(
        client,
        "Fix the crash.",
        mode="do",
        repo=str(project),
        intent="fix",
        worktree=True,
        options={"strategy": "team", "attempts": 3, "profile": "smoke", "allow_web": True},
    )

    argv = argv_of(launcher, run_id)
    assert argv[argv.index("--repo") + 1] == str(project.resolve())
    for flag in ("--mode=fix", "--worktree", "--allow-web"):
        assert flag in argv
    assert argv[argv.index("--strategy") + 1] == "team"
    assert argv[argv.index("--attempts") + 1] == "3"
    assert argv[argv.index("--profile") + 1] == "smoke"
    wait_status(client, run_id, "succeeded")


@pytest.mark.parametrize(
    ("fields", "text"),
    [
        ({"request": None}, "Say what to do"),
        ({"intent": "dance"}, "intent must be one of"),
        ({"repo": "relative"}, "absolute path"),
        ({"options": {"attempts": 12}}, "attempts"),
        ({"options": {"strategy": "magic"}}, "strategy"),
    ],
)
def test_a_do_start_that_cannot_work_is_refused_before_anything_runs(fields, text) -> None:  # noqa: ANN001
    client, launcher = make_client()

    response = client.post(f"{API}/runs", json={"mode": "do", "request": "Do it.", **fields})

    assert response.status_code == 422
    assert text in response.json()["error"]
    assert launcher.records() == []


def test_a_follow_up_is_a_continue_run_in_the_parents_thread() -> None:
    client, launcher = make_client()
    parent = start(client, "Build it.", mode="do")
    wait_status(client, parent, "succeeded")

    response = client.post(
        f"{API}/runs", json={"mode": "continue", "parent": parent, "request": "Now handle empty."}
    )

    assert response.status_code == 202, response.text
    child = response.json()["run_id"]
    argv = argv_of(launcher, child)
    assert argv[argv.index("continue") : argv.index("continue") + 4] == [
        "continue", parent, "--yes", "--non-interactive",
    ]  # fmt: skip
    wait_status(client, child, "succeeded")
    thread = client.get(f"{API}/runs/{child}/results").json()["thread"]
    assert thread["parent"]["run_id"] == parent
    assert (
        client.get(f"{API}/runs/{parent}/results").json()["thread"]["children"][0]["run_id"]
        == child
    )


def test_a_follow_up_can_implement_a_stored_plan_without_new_words() -> None:
    client, launcher = make_client()
    parent = start(client, "Plan it.", mode="do")
    wait_status(client, parent, "succeeded")

    response = client.post(
        f"{API}/runs", json={"mode": "continue", "parent": parent, "implement": True}
    )

    assert response.status_code == 202, response.text
    assert "--implement" in argv_of(launcher, response.json()["run_id"])


def test_a_follow_up_needs_a_parent_that_exists_has_ended_and_words() -> None:
    client, _ = make_client()
    running = start(client, "SLOW", mode="do")
    wait_status(client, running, "running")
    unknown = "20200101-000000-abcdef"

    gone = client.post(f"{API}/runs", json={"mode": "continue", "parent": unknown, "request": "x"})
    busy = client.post(f"{API}/runs", json={"mode": "continue", "parent": running, "request": "x"})
    nothing = client.post(f"{API}/runs", json={"mode": "continue", "request": "x"})

    assert gone.status_code == 422 or gone.status_code == 404
    assert busy.status_code == 409 and "still" in busy.json()["error"]
    assert nothing.status_code == 422
    client.post(f"{API}/runs/{running}/cancel")
