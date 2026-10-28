"""Session accounting and the run profile: sessions, prompt parts, critical path, re-reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from report_helpers import run_dir_in, write_manifest
from test_trajectory import entries_of, run_crew
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import Event, SessionUsage
from engineering_team.pricing import PriceTable, build_table
from engineering_team.report import write_report
from engineering_team.runtime.events import JsonlSink, read_events
from engineering_team.runtime.profile import (
    build_profile,
    critical_path,
    failures,
    rereads,
    sequential_stages,
)
from engineering_team.runtime.profile_text import render_profile
from engineering_team.runtime.prompt_parts import prompt_chars
from engineering_team.runtime.sessions import build_sessions, error_kind, run_sessions
from engineering_team.runtime.trajectory import TrajectoryEntry, TrajectoryRecorder
from engineering_team.testing import ScriptedLLM, ToolCall

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
GOLDEN = Path(__file__).parent / "golden" / "profile.txt"
runner = CliRunner()


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Log:
    """A hand-made event log: ``add(seconds, type, ...)`` numbers events like the sink does."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(
        self,
        seconds: float,
        type: str,
        *,
        stage: str | None = "implement",
        agent: str | None = "Backend Engineer",
        lane: int | None = None,
        emission: int | None = None,
        **data: Any,
    ) -> None:
        if emission is not None:
            data["emission"] = emission
        self.events.append(
            Event(
                seq=len(self.events) + 1,
                ts=at(seconds),
                run_id="r",
                type=type,
                stage=stage,
                agent=agent,
                lane=lane,
                data=data,
            )
        )


def usage(prompt: int, completion: int = 10, cached: int = 0) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_prompt_tokens": cached,
    }


def chars(system: int = 0, tools: int = 0, history: int = 0, results: int = 0) -> dict[str, int]:
    return {"system": system, "tools": tools, "history": history, "results": results}


PRICES: PriceTable = build_table(
    {"scripted/fake": {"input": 1.0, "output": 2.0, "cached_input": 0.1}}
)


def one_session() -> Log:
    log = Log()
    log.add(0, "agent.started", emission=1, task="Build it")
    log.add(
        1, "llm.request", emission=2, model="scripted/fake", prompt_chars=chars(400, 4000, 0, 0)
    )
    log.add(2, "llm.call", emission=3, model="scripted/fake", usage=usage(1000, 50, 0))
    log.add(3, "tool.call", tool="Read", ok=True, trajectory_seq=1)
    log.add(4, "tool.call", tool="Edit", ok=False, trajectory_seq=2)
    log.add(
        5,
        "llm.request",
        emission=4,
        model="scripted/fake",
        prompt_chars=chars(400, 4000, 800, 1200),
    )
    log.add(6, "llm.call", emission=5, model="scripted/fake", usage=usage(2000, 70, 1500))
    log.add(8, "agent.completed", emission=6)
    return log


def failed(seq: int, tool: str, message: str, **args: object) -> TrajectoryEntry:
    return TrajectoryEntry(seq=seq, ts=at(0), kind="tool", tool=tool, args=dict(args),
                           output=message, ok=False)  # fmt: skip


# -- sessions ------------------------------------------------------------------------------------


def test_a_session_counts_its_calls_tokens_cached_tokens_tools_and_cost() -> None:
    trajectory = {2: failed(2, "Edit", "ERROR: old_text was not found in app.py.")}

    (session,) = build_sessions(one_session().events, PRICES, trajectory)

    assert (session.id, session.stage, session.agent, session.task) == (
        "S1", "implement", "Backend Engineer", "Build it",
    )  # fmt: skip
    assert session.seconds == 8 and session.ended_with == "completed"
    assert (session.calls, session.prompt_tokens, session.completion_tokens) == (2, 3000, 120)
    assert session.cached_prompt_tokens == 1500
    assert session.prompt_parts == {
        "system": 200, "tools": 2000, "history": 200, "results": 300, "tool_schema_per_call": 1000,
    }  # fmt: skip
    assert (session.tool_calls, session.tool_failures) == (2, {"edit-no-match": 1})
    # 1500 fresh at $1/M, 1500 cached at $0.1/M, 120 output at $2/M
    assert session.cost_usd == pytest.approx(0.0015 + 0.00015 + 0.00024)


def test_a_failure_without_a_trajectory_entry_is_counted_as_unknown() -> None:
    (session,) = build_sessions(one_session().events, PRICES, None)

    assert session.tool_failures == {"unknown": 1}


def test_an_unpriced_model_has_an_unknown_cost() -> None:
    (session,) = build_sessions(one_session().events, build_table({}), None)

    assert session.cost_usd is None and session.model == "scripted/fake"


def test_a_session_that_never_ended_is_open_until_the_last_event() -> None:
    log = Log()
    log.add(0, "agent.started", emission=1)
    log.add(1, "llm.call", emission=2, model="scripted/fake", usage=usage(100))
    log.add(9, "tool.call", tool="Read", ok=True)

    (session,) = build_sessions(log.events, PRICES)

    assert session.ended_with == "open" and session.seconds == 9 and session.calls == 1


def test_an_agent_error_ends_its_session() -> None:
    log = Log()
    log.add(0, "agent.started", emission=1)
    log.add(2, "agent.error", emission=2, error="boom")

    (session,) = build_sessions(log.events, PRICES)

    assert session.ended_with == "error" and session.seconds == 2


def test_the_same_agent_twice_in_a_stage_is_two_sessions() -> None:
    log = Log()
    for number, (start, end) in enumerate([(0, 3), (5, 9)]):
        log.add(start, "agent.started", emission=10 * number + 1)
        log.add(
            start + 1,
            "llm.call",
            emission=10 * number + 2,
            model="m",
            usage=usage(10 * (number + 1)),
        )
        log.add(end, "agent.completed", emission=10 * number + 3)

    first, second = build_sessions(log.events, PRICES)

    assert (first.id, first.prompt_tokens, first.seconds) == ("S1", 10, 3)
    assert (second.id, second.prompt_tokens, second.seconds) == ("S2", 20, 4)


def test_events_recorded_late_by_a_handler_thread_still_find_their_session() -> None:
    # events.jsonl order is not emission order: the llm.call is logged after agent.completed
    log = Log()
    log.add(0, "agent.started", emission=1)
    log.add(4, "agent.completed", emission=4)
    log.add(4.1, "llm.call", emission=3, model="m", usage=usage(77))

    (session,) = build_sessions(log.events, PRICES)

    assert session.prompt_tokens == 77


def test_parallel_lanes_keep_their_own_calls_and_tools() -> None:
    log = Log()
    log.add(0, "agent.started", lane=1, emission=1)
    log.add(0, "agent.started", lane=2, emission=2)
    log.add(1, "llm.call", lane=1, emission=3, model="m", usage=usage(100))
    log.add(1, "llm.call", lane=2, emission=4, model="m", usage=usage(200))
    log.add(2, "tool.call", lane=2, tool="Read", ok=True)
    log.add(3, "agent.completed", lane=1, emission=5)
    log.add(5, "agent.completed", lane=2, emission=6)

    one, two = build_sessions(log.events, PRICES)

    assert (one.lane, one.prompt_tokens, one.tool_calls, one.seconds) == (1, 100, 0, 3)
    assert (two.lane, two.prompt_tokens, two.tool_calls, two.seconds) == (2, 200, 1, 5)


def test_a_tool_call_is_matched_by_stage_even_when_its_agent_has_another_name() -> None:
    log = Log()
    log.add(0, "agent.started", agent="Backend Engineer", emission=1)
    log.add(1, "tool.call", agent="backend_engineer", tool="Read", ok=True)
    log.add(2, "agent.completed", agent="Backend Engineer", emission=2)

    (session,) = build_sessions(log.events, PRICES)

    assert session.tool_calls == 1


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("ERROR: Path src/web/page.tsx is outside your write scope.", "write-scope"),
        ("ERROR: File not found: nope. Use Find Files to locate it.", "path-not-found"),
        ("ERROR: old_text was not found in app.py.", "edit-no-match"),
        ("ERROR: Command 'rm' is not allowed; allowed: python, pytest", "command-not-allowed"),
        ("ERROR: The command timed out after 300 seconds.", "timeout"),
        ("ERROR: The run was cancelled. Stop working.", "cancelled"),
        (
            "ERROR: K-010 cannot move from in_progress to in_progress. Allowed next states: x",
            "invalid-state",
        ),
        ("ERROR: Path must stay inside the project workspace: /tmp/x", "path-outside-workspace"),
        ("ERROR: No script named 'test' in notes. Available: none.", "nothing-to-run"),
        ("ERROR: Malformed hunk header: '@@'. Use '@@ -start,count'", "bad-arguments"),
        ("ERROR: something odd happened", "other"),
    ],
)
def test_failures_are_classified_by_their_message(message: str, kind: str) -> None:
    assert error_kind(message) == kind


def test_prompt_parts_sort_a_calls_messages_and_schemas() -> None:
    messages = [
        {"role": "system", "content": "s" * 100},
        {"role": "user", "content": "t" * 50},  # the task prompt: counted with the system text
        {"role": "assistant", "content": "a" * 20},
        {"role": "tool", "content": "r" * 30},
        {"role": "user", "content": "Observation: " + "o" * 7},
        {"role": "user", "content": "u" * 10},
    ]

    parts = prompt_chars(messages, [{"type": "function"}])

    assert parts == {
        "system": 150,
        "tools": len('[{"type": "function"}]'),
        "history": 30,
        "results": 50,
    }
    assert prompt_chars("plain prompt", None)["system"] == 12


# -- the profile ---------------------------------------------------------------------------------


def session(
    sid: str, start: float, end: float, stage: str = "s", lane: int | None = None
) -> SessionUsage:
    return SessionUsage(id=sid, stage=stage, lane=lane, started=at(start), ended=at(end),
                        seconds=end - start)  # fmt: skip


def test_the_critical_path_is_the_longest_chain_of_sequential_sessions() -> None:
    sessions = [
        session("S1", 0, 10),
        session("S2", 10, 14, lane=1),  # two lanes after S1: only the longer is on the path
        session("S3", 10, 30, lane=2),
        session("S4", 30, 35),
    ]

    path, seconds = critical_path(sessions)

    assert path == ["S1", "S3", "S4"] and seconds == 35


def test_sessions_that_never_overlap_are_all_on_the_critical_path() -> None:
    sessions = [session("S1", 0, 5), session("S2", 6, 9), session("S3", 9, 12)]

    assert critical_path(sessions) == (["S1", "S2", "S3"], 11)
    assert critical_path([]) == ([], 0.0)


def test_a_stage_that_ran_its_sessions_one_after_another_is_a_parallel_candidate() -> None:
    sessions = [
        session("S1", 0, 5, "implement", lane=1),
        session("S2", 5, 9, "implement", lane=2),
        session("S3", 0, 6, "review", lane=1),
        session("S4", 3, 8, "review", lane=2),  # these two overlapped
        session("S5", 9, 12, "docs"),
    ]

    found = sequential_stages(sessions)

    assert [(f.stage, f.sessions, f.seconds) for f in found] == [("implement", 2, 9)]


def tool_entry(seq: int, tool: str, path: str, stage: str, ok: bool = True) -> TrajectoryEntry:
    return TrajectoryEntry(seq=seq, ts=at(seq), kind="tool", tool=tool, args={"path": path},
                           stage=stage, teammate="dev", ok=ok,
                           output="" if ok else "ERROR: File not found: x")  # fmt: skip


def test_files_read_more_than_once_are_listed_with_how_many_sessions_read_them() -> None:
    entries = [
        tool_entry(1, "Read Project File", "a.py", "spec"),
        tool_entry(2, "Read Project File", "a.py", "implement"),
        tool_entry(3, "Read File Range", "a.py", "implement"),
        tool_entry(4, "Read Project File", "b.py", "implement"),
        tool_entry(5, "Write Project File", "a.py", "implement"),
    ]

    assert [(r.path, r.reads, r.sessions) for r in rereads(entries)] == [("a.py", 3, 2)]


def test_tool_failures_are_grouped_by_tool_and_message() -> None:
    entries = [tool_entry(n, "Read Project File", "x", "s", ok=False) for n in (1, 2)]
    entries.append(tool_entry(3, "Read Project File", "y", "s"))

    (note,) = failures(entries)

    assert (note.tool, note.kind, note.count) == ("Read Project File", "path-not-found", 2)


def test_a_scripted_run_is_profiled_end_to_end(make_context) -> None:  # noqa: ANN001
    ctx = make_context()
    (ctx.workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            ToolCall("Read Project File", {"path": "a.txt"}),
            ToolCall("Read Project File", {"path": "a.txt"}),
            ToolCall("Read Project File", {"path": "missing.txt"}),
            "done",
        ]
    )

    run_crew(ctx, llm)
    profile = build_profile(ctx.run_dir, ctx.prices)

    (only,) = profile.sessions
    assert (only.stage, only.agent, only.calls, only.tool_calls) == (
        "implement", "Backend Engineer", 4, 3,
    )  # fmt: skip
    assert only.tool_failures == {"path-not-found": 1}
    assert only.prompt_parts["system"] > 0 and only.prompt_parts["results"] > 0
    assert only.prompt_parts["tool_schema_per_call"] > 1000  # the real tools' schemas
    assert profile.recorded_prompts and profile.critical_path == ["S1"]
    assert [(r.path, r.reads) for r in profile.rereads] == [("a.txt", 2)]
    assert profile.schema_tokens() == {
        "Backend Engineer": only.prompt_parts["tool_schema_per_call"]
    }
    assert len(entries_of(ctx)) >= 4
    assert [s.id for s in run_sessions(ctx.run_dir, ctx.prices)] == ["S1"]


# -- output --------------------------------------------------------------------------------------


def hand_made_run(directory: Path) -> None:
    """A two-session run with a failed edit, written the way the controller writes it."""

    log = one_session()
    log.add(9, "agent.started", stage="review", agent="Code Reviewer", emission=7)
    log.add(10, "llm.request", stage="review", agent="Code Reviewer", emission=8,
            model="scripted/fake", prompt_chars=chars(800, 2000, 0, 0))  # fmt: skip
    log.add(12, "llm.call", stage="review", agent="Code Reviewer", emission=9,
            model="scripted/fake", usage=usage(500, 30, 0))  # fmt: skip
    log.add(14, "agent.completed", stage="review", agent="Code Reviewer", emission=10)
    sink = JsonlSink(directory / "events.jsonl", directory.name, clock=lambda: T0)
    for event in log.events:
        sink._clock = lambda event=event: event.ts  # type: ignore[assignment]  # noqa: SLF001
        sink.emit(event.type, stage=event.stage, agent=event.agent, **event.data)
    rec = TrajectoryRecorder(directory / "trajectory.jsonl")
    rec.tool_call(tool="Read Project File", args={"path": "app.py"}, output="x", ok=True, ms=5,
                  stage="implement", teammate="dev", ts=at(3))  # fmt: skip
    rec.tool_call(tool="Edit Project File", args={"path": "app.py"}, ok=False, ms=5,
                  output="ERROR: old_text was not found in app.py.", stage="implement",
                  teammate="dev", ts=at(4))  # fmt: skip


def test_the_profile_text_matches_the_golden_output(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    hand_made_run(directory)

    text = render_profile(build_profile(directory, PRICES))

    assert text == GOLDEN.read_text(encoding="utf-8")


def test_a_run_without_sessions_says_so(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)

    assert "no agent sessions were recorded" in render_profile(build_profile(directory, PRICES))


def test_a_run_from_before_prompt_sizing_still_profiles(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    hand_made_run(directory)
    kept = [e for e in read_events(directory / "events.jsonl") if e.type != "llm.request"]
    (directory / "events.jsonl").write_text(
        "".join(e.model_dump_json() + "\n" for e in kept), encoding="utf-8"
    )

    text = render_profile(build_profile(directory, PRICES))

    assert "not recorded for this run" in text and "S1" in text


def invoke(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["--workspace-root", str(Path.cwd() / "ws"), *args])


def test_the_profile_command_prints_text_and_json() -> None:
    directory = run_dir_in(Path.cwd())
    write_manifest(directory)
    hand_made_run(directory)

    text = invoke("profile")
    data = json.loads(invoke("profile", "--format", "json").stdout)

    assert (
        text.exit_code == 0 and "Sessions by cost" in text.stdout and "Critical path" in text.stdout
    )
    assert data["run_id"] == directory.name and len(data["sessions"]) == 2
    assert data["critical_path"]["sessions"] == ["S1", "S2"]
    assert json.loads(invoke("--json", "profile").stdout)["model_calls"] == 3
    assert invoke("profile", "--format", "xml").exit_code == 2


def test_the_report_has_a_section_on_where_the_time_and_money_went(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory)
    hand_made_run(directory)

    md = write_report(directory, "md").read_text(encoding="utf-8")
    html = write_report(directory, "html").read_text(encoding="utf-8")

    assert "## Where the time and money went" in md and "| S1 |" in md
    assert 'id="profile"' in html and "Where the time and money went" in html
    assert "Backend Engineer" in html
