"""The event reader behind SSE and the live views: partial lines, resuming, and the digest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from engineering_team.pricing import PriceTable, default_prices  # noqa: E402
from engineering_team.runtime.events import JsonlSink  # noqa: E402
from engineering_team.ui.eventlog import STRIDE, EventLog  # noqa: E402


def make(tmp_path: Path) -> tuple[JsonlSink, EventLog]:
    path = tmp_path / "events.jsonl"
    return JsonlSink(path, "run"), EventLog(path, PriceTable(default_prices()))


def test_a_line_still_being_written_is_not_read_until_it_is_whole(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    sink.emit("run.started")
    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b'{"seq": 2, "ts": "2026-01-01T00:00:00Z", "run_id": "run", "ty')

    assert [seq for seq, _ in log.read_after(0)] == [1]

    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b'pe": "x", "data": {}}\n')

    assert [seq for seq, _ in log.read_after(0)] == [1, 2]


def test_read_after_honours_after_and_limit(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    for n in range(STRIDE * 3 + 5):
        sink.emit("tool.call", agent="a", tool="t", n=n)

    assert [s for s, _ in log.read_after(0, 3)] == [1, 2, 3]
    assert [s for s, _ in log.read_after(STRIDE, 2)] == [STRIDE + 1, STRIDE + 2]
    assert [s for s, _ in log.read_after(STRIDE * 3 + 4)] == [STRIDE * 3 + 5]
    assert log.read_after(STRIDE * 3 + 5) == []
    assert json.loads(log.read_after(STRIDE * 2, 1)[0][1])["data"]["n"] == STRIDE * 2


def test_an_unreadable_line_is_skipped_but_the_rest_still_flows(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    sink.emit("a")
    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b"this is not json\n")
    sink.emit("b")

    assert [json.loads(line)["type"] for _, line in log.read_after(0)] == ["a", "b"]


def test_the_digest_follows_questions_tools_and_the_run_lifecycle(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    sink.emit("run.started")
    sink.emit("tool.call", agent="backend", tool="Run Tests", ok=False, duration=2.0, lane=1)
    sink.emit("question", question_id="Q-001", text="Which?", agent="backend", interactive=True)
    sink.emit("question", question_id="Q-002", text="Mute?", agent="qa", interactive=False)

    digest = log.digest()
    assert digest.agents["backend"].failed == 1 and digest.agents["backend"].last.lane == "1"  # type: ignore[union-attr]
    assert list(digest.questions) == ["Q-001"]  # a question nobody can answer is not open
    assert digest.finished is False

    sink.emit("question.answered", question_id="Q-001")
    sink.emit("run.cancel_requested")
    sink.emit("run.finished", status="cancelled")
    digest = log.digest()
    assert digest.questions == {} and digest.cancel_requested and digest.finished
    sink.emit("run.started", resumed=1)  # a resume reopens the run
    digest = log.digest()
    assert not digest.finished and not digest.cancel_requested


def test_a_replaced_file_is_read_again_from_the_start(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    for _ in range(5):
        sink.emit("tool.call", agent="a", tool="t")
    assert log.digest().agents["a"].calls == 5

    (tmp_path / "events.jsonl").write_text("")
    fresh = JsonlSink(tmp_path / "events.jsonl", "run")
    fresh.emit("tool.call", agent="a", tool="t")

    assert log.digest().agents["a"].calls == 1


def test_usage_is_rebuilt_from_the_events(tmp_path: Path) -> None:
    sink, log = make(tmp_path)
    sink.emit(
        "llm.call",
        agent="a",
        model="openai/gpt-x",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    sink.emit("tool.call", agent="a", tool="t")

    report = log.usage()

    assert report.totals.total_tokens == 15 and report.tool_calls == 1
    assert report.estimated_cost_usd is None  # an unknown model has no price, never $0


def test_a_missing_file_is_an_empty_log(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "none.jsonl", PriceTable(default_prices()))

    assert log.read_after(0) == [] and log.digest().last_seq == 0
