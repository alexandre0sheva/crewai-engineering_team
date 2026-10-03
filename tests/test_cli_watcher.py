"""``RunWatcher`` reads a run directory as it grows; ``describe_event`` words the feed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from engineering_team.cli.snapshot import RunWatcher, describe_event
from engineering_team.contracts import Event
from engineering_team.pricing import build_table

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def event(seq: int, type: str, **fields: object) -> str:
    data = fields.pop("data", {})
    payload = Event(seq=seq, ts=NOW, run_id="r1", type=type, data=data, **fields)  # type: ignore[arg-type]
    return payload.model_dump_json() + "\n"


def append(run_dir: Path, *lines: str) -> None:
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.writelines(lines)


def watcher(tmp_path: Path) -> RunWatcher:
    return RunWatcher(tmp_path, build_table(), clock=lambda: NOW)


def test_a_missing_run_directory_is_an_empty_view(tmp_path: Path) -> None:
    state = watcher(tmp_path / "nothing").poll()

    assert state.cards == [] and state.status == "unknown" and state.feed == []
    assert state.progress.overall_percent == 0.0


def test_each_poll_reads_only_what_is_new(tmp_path: Path) -> None:
    w = watcher(tmp_path)
    append(tmp_path, event(1, "stage.started", stage="spec"))
    assert [line.text for line in w.poll().feed] == ["stage spec started"]
    assert w.poll().feed[-1].text == "stage spec started" and w.new_lines == []

    append(tmp_path, event(2, "stage.finished", stage="spec", data={"status": "succeeded"}))
    state = w.poll()

    assert [line.text for line in w.new_lines] == ["stage spec succeeded"]
    assert len(state.feed) == 2 and state.stage == "spec"


def test_a_half_written_line_waits_for_the_rest(tmp_path: Path) -> None:
    w = watcher(tmp_path)
    whole = event(1, "stage.started", stage="plan")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(whole[:20])
    assert w.poll().feed == []

    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(whole[20:])

    assert [line.text for line in w.poll().feed] == ["stage plan started"]


def test_garbage_lines_are_skipped(tmp_path: Path) -> None:
    append(tmp_path, "not json\n", '{"seq": "x"}\n', event(1, "run.started"))

    assert [line.text for line in watcher(tmp_path).poll().feed] == ["run started"]


def test_usage_adds_up_from_model_call_events(tmp_path: Path) -> None:
    call = {
        "model": "openai/gpt-6-luna",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }
    append(
        tmp_path,
        event(1, "llm.call", data=call),
        event(2, "llm.call", data=call),
        event(3, "tool.call", data={"tool": "Write Project File", "ok": True}),
    )

    state = watcher(tmp_path).poll()

    assert state.usage.totals.total_tokens == 2400 and state.usage.totals.calls == 2
    assert state.usage.tool_calls == 1


def test_lanes_follow_parallel_work_and_show_the_latest_tool(tmp_path: Path) -> None:
    w = watcher(tmp_path)
    append(
        tmp_path,
        event(1, "lane.started", lane=1, data={"unit": "WP-1", "kind": "package"}),
        event(2, "lane.started", lane=2, data={"unit": "WP-2", "kind": "package"}),
        event(
            3, "tool.call", lane=1, agent="backend_engineer", data={"tool": "Run Tests", "ok": True}
        ),
    )

    lanes = w.poll().lanes
    assert [(lane.lane, lane.unit, lane.last_tool, lane.agent) for lane in lanes] == [
        ("1", "WP-1", "Run Tests", "backend_engineer"),
        ("2", "WP-2", "", ""),
    ]

    append(
        tmp_path, event(4, "lane.finished", lane=1, data={"unit": "WP-1", "status": "succeeded"})
    )
    assert [lane.lane for lane in w.poll().lanes] == ["2"]


def test_the_manifest_and_board_are_read_when_present(tmp_path: Path) -> None:
    (tmp_path / "board.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "paused": True,
                "cards": [{"id": "K-001", "title": "Spec", "kind": "stage", "status": "done"}],
            }
        ),
        encoding="utf-8",
    )

    state = watcher(tmp_path).poll()

    assert state.paused and state.progress.overall_percent == 100.0 and state.cards[0].id == "K-001"


def test_the_feed_words_the_events_people_care_about() -> None:
    def line(type: str, **fields: object) -> str | None:
        data = fields.pop("data", {})
        found = describe_event(Event(seq=1, ts=NOW, run_id="r", type=type, data=data, **fields))  # type: ignore[arg-type]
        return found.text if found else None

    assert (
        line("check.finished", data={"check": "tests", "status": "failed"}) == "check tests failed"
    )
    assert (
        line("verify.repair", data={"round": 2, "checks": ["tests"]})
        == "repair round 2 for ['tests']"
    )
    assert (
        line(
            "board.card_moved",
            data={"card_id": "K-004", "from_status": "ready", "to_status": "in_progress"},
        )
        == "K-004 ready → in_progress"
    )
    assert (
        line("tool.call", lane=2, agent="qa", data={"tool": "Run Tests", "ok": False})
        == "[lane 2] qa Run Tests (error)"
    )
    assert line("run.finished", data={"status": "failed"}) == "run failed"
    assert line("llm.call", data={}) is None  # chatty events stay out of the feed
