"""Runs over HTTP: start (JSON and uploads), watch, stream, cancel, resume, two at once."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from ui_helpers import (  # noqa: E402
    API,
    make_client,
    serve,
    sse_events,
    start,
    status_of,
    wait_for,
    wait_status,
)

from engineering_team.runtime.run_store import RunStore  # noqa: E402


def test_a_run_started_over_http_streams_events_and_ends(tmp_path: Path) -> None:
    client, _ = make_client()
    run_id = start(client)

    wait_status(client, run_id, "succeeded")
    body = client.get(f"{API}/runs/{run_id}/events?follow=false").text
    frames = sse_events(body)
    kinds = [frame["data"]["type"] for frame in frames]

    assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
    assert "tool.call" in kinds and "board.card_moved" in kinds
    assert [int(f["id"]) for f in frames] == list(range(1, len(frames) + 1))  # id is seq
    assert all(f["data"]["run_id"] == run_id for f in frames)


def test_following_a_finished_run_replays_and_then_says_it_is_over() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")

    body = client.get(f"{API}/runs/{run_id}/events").text  # follow is the default

    assert body.rstrip().endswith("event: end\ndata: {}")
    assert sse_events(body)[0]["data"]["type"] == "run.started"


def test_a_dropped_stream_resumes_from_last_event_id() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    everything = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
    cut = everything[4]["id"]

    resumed = sse_events(
        client.get(f"{API}/runs/{run_id}/events?follow=false", headers={"Last-Event-ID": cut}).text
    )
    by_query = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&after={cut}").text)

    assert [f["id"] for f in resumed] == [f["id"] for f in everything[5:]]
    assert resumed == by_query
    assert not sse_events(
        client.get(
            f"{API}/runs/{run_id}/events?follow=false",
            headers={"Last-Event-ID": everything[-1]["id"]},
        ).text
    )


def test_resume_works_across_the_index_stride() -> None:
    """A long log is served from the right line however far the client had got."""

    from engineering_team.runtime.events import JsonlSink
    from engineering_team.ui import eventlog

    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    run_dir = RunStore(Path.cwd() / "ws" / "demo").run_dir(run_id)
    sink = JsonlSink(run_dir / "events.jsonl", run_id)
    for number in range(eventlog.STRIDE * 2 + 10):
        sink.emit("tool.call", agent="backend", tool="T", n=number)

    total = len((run_dir / "events.jsonl").read_text().splitlines())
    after = eventlog.STRIDE + 7
    rest = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&after={after}").text)

    assert [int(f["id"]) for f in rest][:3] == [after + 1, after + 2, after + 3]
    assert int(rest[-1]["id"]) == total


def test_events_can_be_filtered_by_type_prefix() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")

    frames = sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false&types=board.").text)

    assert frames and all(f["data"]["type"].startswith("board.") for f in frames)


def test_a_live_stream_follows_the_run_and_ends_when_it_does() -> None:
    client, _ = make_client()
    run_id = start(client, "SLOW")
    wait_status(client, run_id, "running")
    seen: list[str] = []

    with serve(client) as live, live.stream("GET", f"{API}/runs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                seen.append(json.loads(line[6:])["type"])
            if len(seen) == 3:  # events arrive while the run is still going
                assert status_of(client, run_id) == "running"
                live.post(f"{API}/runs/{run_id}/cancel")
            if line.startswith("event: end"):
                break

    assert seen[0] == "run.started" and seen[-1] == "run.finished"
    assert status_of(client, run_id) == "cancelled"


def test_a_live_stream_picks_up_where_a_dropped_one_stopped() -> None:
    client, _ = make_client()
    run_id = start(client, "SLOW")
    wait_status(client, run_id, "running")
    first: list[int] = []

    with serve(client) as live:
        with live.stream("GET", f"{API}/runs/{run_id}/events") as response:
            for line in response.iter_lines():
                if line.startswith("id: "):
                    first.append(int(line[4:]))
                if len(first) == 4:
                    break  # the connection drops here
        last = str(first[-1])
        live.post(f"{API}/runs/{run_id}/cancel")
        again: list[int] = []
        with live.stream(
            "GET", f"{API}/runs/{run_id}/events", headers={"Last-Event-ID": last}
        ) as response:
            for line in response.iter_lines():
                if line.startswith("id: "):
                    again.append(int(line[4:]))
                if line.startswith("event: end"):
                    break

    assert again[0] == int(last) + 1 and again == list(range(again[0], again[-1] + 1))


def test_a_run_can_be_cancelled_and_then_resumed() -> None:
    client, _ = make_client()
    run_id = start(client, "SLOW")
    wait_status(client, run_id, "running")

    cancelled = client.post(f"{API}/runs/{run_id}/cancel")

    assert cancelled.status_code == 200
    assert "Cancellation requested" in cancelled.json()["message"]
    wait_status(client, run_id, "cancelled")
    assert (
        client.post(f"{API}/runs/{run_id}/cancel").json()["message"].endswith("nothing to cancel.")
    )

    resumed = client.post(f"{API}/runs/{run_id}/resume")

    assert resumed.status_code == 202
    wait_status(client, run_id, "succeeded")
    data = client.get(f"{API}/runs/{run_id}").json()
    assert data["manifest"]["resumes"] == 1 and data["process"]["resume"] is True
    kinds = [
        f["data"]["type"]
        for f in sse_events(client.get(f"{API}/runs/{run_id}/events?follow=false").text)
    ]
    assert kinds.count("run.started") == 2 and kinds.count("run.finished") == 2


def test_only_a_run_that_did_not_succeed_can_be_resumed() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")

    response = client.post(f"{API}/runs/{run_id}/resume")

    assert response.status_code == 409 and "succeeded" in response.json()["error"]
    assert client.post(f"{API}/runs/20250101-000000-abcdef/resume").status_code == 404


def test_two_runs_go_at_once_and_a_third_waits_for_a_free_slot() -> None:
    client, launcher = make_client(max_concurrent=2)
    first = start(client, "SLOW", project_name="alpha")
    second = start(client, "SLOW", project_name="beta")
    wait_status(client, first, "running")
    wait_status(client, second, "running")

    assert {r.run_id for r in launcher.active()} == {first, second}
    refused = client.post(
        f"{API}/runs", json={"mode": "new", "request": "x", "project_name": "gamma"}
    )

    assert refused.status_code == 429 and "ui.max_concurrent_runs" in refused.json()["error"]
    client.post(f"{API}/runs/{first}/cancel")
    wait_status(client, first, "cancelled")
    wait_for(lambda: len(launcher.active()) == 1, "the slot to free")
    third = start(client, project_name="gamma")
    wait_status(client, third, "succeeded")
    client.post(f"{API}/runs/{second}/cancel")
    wait_status(client, second, "cancelled")
    runs = client.get(f"{API}/runs").json()
    assert [r["run_id"] for r in runs] == sorted([first, second, third])
    assert {r["project"] for r in runs} == {"alpha", "beta", "gamma"}


def test_the_workspace_lock_lets_one_run_write_a_project() -> None:
    client, _ = make_client()
    first = start(client, "SLOW")
    wait_status(client, first, "running")

    second = start(client)  # the same project, while the first holds it
    wait_status(client, second, "failed")
    view = client.get(f"{API}/runs/{second}").json()

    assert "in use by run" in view["error"] and "only one run can write" in view["error"]
    assert view["manifest"] is None and view["process"]["alive"] is False
    client.post(f"{API}/runs/{first}/cancel")
    wait_status(client, first, "cancelled")


def test_a_run_that_dies_before_writing_a_manifest_is_explained() -> None:
    client, _ = make_client()
    run_id = start(client, "FAIL-AT-ONCE")

    wait_status(client, run_id, "failed")
    view = client.get(f"{API}/runs/{run_id}").json()

    assert "told to fail at once" in view["error"]
    assert client.post(f"{API}/runs/{run_id}/cancel").status_code == 404
    listed = [r for r in client.get(f"{API}/runs").json() if r["run_id"] == run_id]
    assert listed and listed[0]["status"] == "failed"
    frames = sse_events(client.get(f"{API}/runs/{run_id}/events").text)
    assert [f["event"] for f in frames] == ["end"]  # nothing to replay, and the stream ends


def test_the_run_view_carries_live_state(tmp_path: Path) -> None:
    client, _ = make_client()
    run_id = start(client, "SLOW")
    wait_status(client, run_id, "running")

    view = wait_for(
        lambda: (v := client.get(f"{API}/runs/{run_id}").json())["last_seq"] > 3 and v,
        "events to arrive",
    )

    assert view["project"] == "demo" and view["manifest"]["mode"] == "new"
    assert view["process"]["alive"] is True and view["paused"] is False
    assert view["usage"]["tool_calls"] >= 1
    assert view["progress"]["cards_total"] == 1
    client.post(f"{API}/runs/{run_id}/cancel")
    wait_status(client, run_id, "cancelled")


def test_unknown_and_malformed_run_ids_are_404s() -> None:
    client, _ = make_client()

    for run_id in ("20250101-000000-abcdef", "../../etc", "nope", "2025"):
        assert client.get(f"{API}/runs/{run_id}").status_code == 404
    assert client.get(f"{API}/runs/20250101-000000-abcdef/events").status_code == 404
    assert client.get(f"{API}/runs/20250101-000000-abcdef/board").status_code == 404


# -- the start request -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"mode": "new"}, "Say what to do"),
        ({"mode": "bogus", "request": "x"}, "mode"),
        ({"mode": "new", "request": "x", "surprise": 1}, "surprise"),
        ({"mode": "feature", "request": "x"}, "repo"),
        ({"mode": "feature", "request": "x", "repo": "relative/dir"}, "absolute"),
        ({"mode": "feature", "request": "x", "repo": "/no/such/dir/at/all"}, "existing directory"),
        ({"mode": "maintain", "repo": "{tmp}"}, "task"),
        (
            {"mode": "new", "request": "x", "options": {"budget": {"max_cost_usd": -1}}},
            "max_cost_usd",
        ),
        ({"mode": "new", "request": "x", "options": {"sandbox": "chroot"}}, "sandbox"),
        ({"mode": "new", "request": "x", "options": {"max_parallel_agents": 0}}, "max_parallel"),
    ],
)
def test_a_bad_start_is_a_422_that_says_what_to_fix(
    body: dict[str, object], fragment: str, tmp_path: Path
) -> None:
    client, launcher = make_client()
    body = json.loads(json.dumps(body).replace("{tmp}", str(tmp_path)))

    response = client.post(f"{API}/runs", json=body)

    assert response.status_code == 422, response.text
    assert fragment in response.json()["error"]
    assert launcher.records() == []  # no process was started


def test_options_become_arguments_and_overrides_for_the_process(tmp_path: Path) -> None:
    client, launcher = make_client()
    repo = tmp_path / "project"
    repo.mkdir()

    response = client.post(
        f"{API}/runs",
        json={
            "mode": "feature",
            "request": "Add search",
            "repo": str(repo),
            "worktree": True,
            "squash": True,
            "options": {
                "provider": "anthropic",
                "profile": "smoke",
                "allow_web": True,
                "sandbox": "docker",
                "budget": {"max_cost_usd": 2.5, "max_tool_calls": 300},
                "max_parallel_agents": 2,
                "disabled_teammates": ["code_reviewer"],
            },
        },
    )

    assert response.status_code == 202, response.text
    argv = launcher.record(response.json()["run_id"]).argv  # type: ignore[union-attr]
    assert argv[argv.index("--run-id") + 1] == response.json()["run_id"]
    assert "--answers-via-inbox" in argv and "feature" in argv
    for flag, value in (("--repo", str(repo.resolve())), ("--provider", "anthropic"),
                        ("--profile", "smoke"), ("--sandbox", "docker")):  # fmt: skip
        assert argv[argv.index(flag) + 1] == value
    assert {"--worktree", "--squash", "--allow-web"} <= set(argv)
    assert "--strategy" not in argv  # repository modes always use the pipeline


def test_the_overrides_reach_the_process_as_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team.settings import OVERRIDES_ENV, load_settings
    from engineering_team.ui.launcher import RunOptions, overrides_for

    found = overrides_for(
        RunOptions.model_validate(
            {
                "budget": {"max_cost_usd": 2.5},
                "max_parallel_agents": 2,
                "disabled_teammates": ["qa"],
            }
        )
    )
    monkeypatch.setenv(OVERRIDES_ENV, json.dumps(found))
    settings = load_settings()

    assert found == {
        "budget.max_cost_usd": 2.5, "parallel.max_parallel_agents": 2, "team.qa.enabled": False,
    }  # fmt: skip
    assert settings.budget.max_cost_usd == 2.5 and settings.parallel.max_parallel_agents == 2
    assert settings.source_of("budget.max_cost_usd") == f"env {OVERRIDES_ENV}"


def test_request_text_and_uploads_are_saved_where_the_cli_can_read_them() -> None:
    client, launcher = make_client()

    response = client.post(
        f"{API}/runs",
        data={"spec": json.dumps({"mode": "new", "request": "Typed part."})},
        files=[
            ("request_files", ("../../brief.md", b"Uploaded part.", "text/markdown")),
            ("context_files", ("notes.txt", b"Reference.", "text/plain")),
        ],
    )

    assert response.status_code == 202, response.text
    record = launcher.record(response.json()["run_id"])
    assert record is not None
    files = [Path(record.argv[i + 1]) for i, a in enumerate(record.argv) if a == "--request-file"]
    assert sorted(f.name for f in files) == ["brief.md", "request.md"]  # a bare name, no ../
    assert {f.read_text() for f in files} == {"Typed part.", "Uploaded part."}
    assert all(launcher.state_dir in f.parents for f in files)  # never outside the UI's own folder
    context = Path(record.argv[record.argv.index("--context-dir") + 1])
    assert (context / "notes.txt").read_text() == "Reference."
    wait_status(client, response.json()["run_id"], "succeeded")


@pytest.mark.parametrize(
    ("name", "data", "fragment"),
    [
        ("malware.exe", b"MZ", "not an accepted file"),
        ("..", b"x", "not an accepted file"),
        ("brief.md", b"\xff\xfe\x00bad", "UTF-8"),
        ("brief.md", b"text\x00more", "not a text file"),
    ],
)
def test_bad_uploads_are_refused(name: str, data: bytes, fragment: str) -> None:
    client, launcher = make_client()

    response = client.post(
        f"{API}/runs",
        data={"spec": json.dumps({"mode": "new"})},
        files=[("request_files", (name, data, "text/plain"))],
    )

    assert response.status_code == 422 and fragment in response.json()["error"]
    assert launcher.records() == []


def test_a_multipart_start_without_a_spec_is_refused() -> None:
    client, _ = make_client()

    response = client.post(f"{API}/runs", data={"other": "x"},
                           files=[("request_files", ("a.md", b"x", "text/plain"))])  # fmt: skip

    assert response.status_code == 422 and "spec" in response.json()["error"]


def test_a_list_of_runs_is_filtered_and_limited() -> None:
    client, _ = make_client()
    for name in ("one", "two"):
        wait_status(client, start(client, project_name=name), "succeeded")

    assert {r["project"] for r in client.get(f"{API}/runs?project=one").json()} == {"one"}
    assert len(client.get(f"{API}/runs?limit=1").json()) == 1


def test_concurrent_http_starts_do_not_share_a_run_id() -> None:
    client, _ = make_client(max_concurrent=8)
    ids: list[str] = []

    def go(name: str) -> None:
        ids.append(start(client, project_name=name))

    threads = [threading.Thread(target=go, args=(f"p{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(ids)) == 4
    for run_id in ids:
        wait_status(client, run_id, "succeeded")


def test_free_text_cannot_become_an_option_of_the_command(tmp_path: Path) -> None:
    client, launcher = make_client()
    repo = tmp_path / "project"
    repo.mkdir()

    response = client.post(
        f"{API}/runs",
        json={
            "mode": "review",
            "repo": str(repo),
            "base": "--help",
            "focus": "--config=/etc/passwd",
            "project_name": "--reset",
        },
    )

    assert response.status_code == 202, response.text
    argv = launcher.record(response.json()["run_id"]).argv  # type: ignore[union-attr]
    assert "--base=--help" in argv and "--focus=--config=/etc/passwd" in argv
    assert "--project-name=--reset" in argv
    assert "--help" not in argv and "--reset" not in argv and "--config=/etc/passwd" not in argv
