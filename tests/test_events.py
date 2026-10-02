from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from engineering_team.runtime.events import (
    REDACTED,
    FanoutSink,
    JsonlSink,
    NullSink,
    Scrubber,
    read_events,
)
from engineering_team.settings import secret_values

KEY = "sk-live-0123456789abcdef"


def _fixed_clock() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_events_are_one_json_object_per_line_with_lifted_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, "run-1", clock=_fixed_clock)

    sink.emit(
        "tool.call", tool="Read Project File", ok=True, stage="build", agent="Backend", lane=2
    )
    sink.emit("run.note", text="héllo ✓")

    first, second = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert first == {
        "schema_version": 1,
        "seq": 1,
        "ts": "2026-01-02T03:04:05Z",
        "run_id": "run-1",
        "type": "tool.call",
        "stage": "build",
        "agent": "Backend",
        "lane": 2,
        "data": {"tool": "Read Project File", "ok": True},
    }
    assert second["seq"] == 2 and second["stage"] is None and second["data"] == {"text": "héllo ✓"}


def test_events_read_back_as_contract_objects(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "events.jsonl", "run-1")
    sink.emit("a", n=1)
    sink.emit("b", n=2)

    events = list(read_events(tmp_path / "events.jsonl"))

    assert [(e.seq, e.type, e.data["n"]) for e in events] == [(1, "a", 1), (2, "b", 2)]
    assert all(e.run_id == "run-1" and e.ts.tzinfo is not None for e in events)


def test_a_reopened_log_continues_the_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    JsonlSink(path, "run-1").emit("first")

    JsonlSink(path, "run-1").emit("second")

    assert [e.seq for e in read_events(path)] == [1, 2]


def test_concurrent_emitters_never_interleave_lines_and_seq_matches_file_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, "run-1")
    threads_count, per_thread = 8, 200
    padding = "x" * 2000  # long lines make torn writes obvious

    def emit(worker: int) -> None:
        for number in range(per_thread):
            sink.emit("load", worker=worker, number=number, padding=padding)

    threads = [threading.Thread(target=emit, args=(n,)) for n in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]  # any torn line would raise here
    assert len(parsed) == threads_count * per_thread
    assert [event["seq"] for event in parsed] == list(range(1, len(parsed) + 1))
    for worker in range(threads_count):
        numbers = [e["data"]["number"] for e in parsed if e["data"]["worker"] == worker]
        assert numbers == list(range(per_thread))  # each thread's own order is preserved


def test_a_torn_or_corrupt_line_does_not_hide_the_rest_of_the_log(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, "run-1")
    sink.emit("good")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "type": "torn\n\nnot json\n')
    sink.emit("after")

    assert [e.type for e in read_events(path)] == ["good", "after"]
    assert list(read_events(tmp_path / "missing.jsonl")) == []


def test_unserialisable_data_is_stringified_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, "run-1")

    sink.emit("odd", path=Path("/a/b"), kind=object, items=[1, 2])

    event = next(iter(read_events(path)))
    assert event.data["path"] == "/a/b" and event.data["items"] == [1, 2]


def test_a_sink_that_cannot_write_does_not_raise(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("a file, not a directory", encoding="utf-8")

    JsonlSink(blocked / "events.jsonl", "run-1").emit("lost")  # must not raise


# -- scrubbing ------------------------------------------------------------------------------


def test_the_scrubber_replaces_secrets_in_nested_structures() -> None:
    scrubber = Scrubber([KEY])

    cleaned = scrubber.scrub(
        {"msg": f"Authorization: Bearer {KEY}", "nested": [{"k": KEY}, (KEY,)], "n": 3, KEY: "v"}
    )

    assert cleaned == {
        "msg": f"Authorization: Bearer {REDACTED}",
        "nested": [{"k": REDACTED}, [REDACTED]],
        "n": 3,
        REDACTED: "v",
    }


def test_short_values_are_not_scrubbed_and_longer_secrets_win() -> None:
    scrubber = Scrubber(["true", "abc", "token-abcdefgh", "token-abcdefgh-extended"])

    assert scrubber.scrub("true abc") == "true abc"
    assert scrubber.scrub("x token-abcdefgh-extended y") == f"x {REDACTED} y"
    assert not Scrubber(["short"]) and Scrubber([KEY])


def test_secrets_never_reach_the_event_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, "run-1", scrubber=Scrubber([KEY]))

    sink.emit("tool.call", args={"command": f"curl -H 'x: {KEY}'"}, agent=f"agent-{KEY}")
    sink.emit("llm.call", error=ValueError(f"bad key {KEY}"))  # stringified, then scrubbed

    raw = path.read_text(encoding="utf-8")
    assert KEY not in raw and raw.count(REDACTED) == 3


def test_secret_values_come_from_credential_like_environment_names() -> None:
    env = {
        "OPENAI_API_KEY": KEY,
        "GITHUB_TOKEN": "ghp_abcdefghijkl",
        "MY_SECRET": "s3cr3t-value",
        "DB_PASSWORD": "hunter2hunter2",
        "PATH": "/usr/bin:/bin",
        "EMPTY_KEY": "",
        "HOME": "/home/me",
    }

    assert secret_values(env) == {KEY, "ghp_abcdefghijkl", "s3cr3t-value", "hunter2hunter2"}


def test_a_run_context_scrubs_ambient_secrets_from_its_event_log(make_context, monkeypatch) -> None:
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok-abcdefghij123456")
    ctx = make_context()

    ctx.events.emit("probe", text="leaked tok-abcdefghij123456?")

    raw = (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "tok-abcdefghij123456" not in raw and REDACTED in raw


# -- fan-out ----------------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    def emit(self, type: str, **data: object) -> None:
        self.seen.append((type, data))


class _Exploding:
    def emit(self, type: str, **data: object) -> None:
        raise RuntimeError("sink down")


def test_fanout_delivers_to_every_sink_even_if_one_fails() -> None:
    first, second = _Recorder(), _Recorder()
    fanout = FanoutSink(first, _Exploding(), second)
    third = _Recorder()
    fanout.add(third)

    fanout.emit("x", value=1)

    assert first.seen == second.seen == third.seen == [("x", {"value": 1})]


def test_the_null_sink_accepts_anything() -> None:
    NullSink().emit("whatever", a=1)
