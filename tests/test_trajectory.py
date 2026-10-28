"""The trajectory log (what tools returned, what models said) and the ``trace`` command."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cli_helpers import latest_run, project, run_new, workspace_root
from crewai import Crew, Process
from pipeline_fakes import FakeRunner
from report_helpers import run_dir_in, write_manifest
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.report import write_report
from engineering_team.report.collect import build_report
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import Scrubber, read_events, stage_scope
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.trace import render_text
from engineering_team.runtime.trajectory import (
    BUDGET_OUTPUT_CHARS,
    MIN_OUTPUT_CHARS,
    TrajectoryEntry,
    TrajectoryRecorder,
    read_entry,
    read_trajectory,
)
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, build_agent, build_task
from engineering_team.tools.registry import build_tools

MakeContext = Callable[..., RunContext]
GOLDEN = Path(__file__).parent / "golden" / "trace.txt"
T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
runner = CliRunner()


def recorder(tmp_path: Path, **options: object) -> TrajectoryRecorder:
    return TrajectoryRecorder(tmp_path / "trajectory.jsonl", **options)  # type: ignore[arg-type]


def call(rec: TrajectoryRecorder, output: str = "ok", **fields: object) -> int | None:
    values: dict[str, object] = {"tool": "Read", "args": {}, "ok": True, "ms": 5, **fields}
    return rec.tool_call(output=output, **values)  # type: ignore[arg-type]


# -- the recorder --------------------------------------------------------------------------------


def test_a_tool_call_is_one_line_with_its_output(tmp_path: Path) -> None:
    rec = recorder(tmp_path)

    seq = call(rec, "hello", tool="Read Project File", args={"path": "a.txt"}, teammate="dev")

    (entry,) = read_trajectory(rec.path)
    assert seq == entry.seq == 1
    assert (entry.kind, entry.tool, entry.args, entry.output) == (
        "tool",
        "Read Project File",
        {"path": "a.txt"},
        "hello",
    )
    assert (entry.teammate, entry.ok, entry.ms, entry.truncated) == ("dev", True, 5, False)
    assert len(rec.path.read_text(encoding="utf-8").splitlines()) == 1


def test_messages_are_recorded_and_blank_ones_are_not(tmp_path: Path) -> None:
    rec = recorder(tmp_path)

    assert rec.message(role="assistant", text="   ") is None
    seq = rec.message(role="final", text="All done.", teammate="dev")

    (entry,) = read_trajectory(rec.path)
    assert seq == 1 and (entry.kind, entry.role, entry.text) == ("message", "final", "All done.")


def test_a_second_recorder_continues_the_file(tmp_path: Path) -> None:
    call(recorder(tmp_path))
    call(recorder(tmp_path))

    assert [entry.seq for entry in read_trajectory(tmp_path / "trajectory.jsonl")] == [1, 2]


def test_a_torn_last_line_is_skipped_and_entries_are_found_by_seq(tmp_path: Path) -> None:
    rec = recorder(tmp_path)
    call(rec, "first")
    call(rec, "second")
    with rec.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":3,"ts":"2026')  # a crash cut the line

    assert [e.output for e in read_trajectory(rec.path)] == ["first", "second"]
    found = read_entry(rec.path, 2)
    assert found is not None and found.output == "second"
    assert read_entry(rec.path, 9) is None


def test_a_secret_never_reaches_the_file(tmp_path: Path) -> None:
    secret = "sk-test-0123456789abcdef"
    rec = recorder(tmp_path, scrubber=Scrubber([secret]))

    call(rec, f"API_KEY={secret}\nmore", args={"command": f"curl -H 'x: {secret}'"})
    rec.message(role="assistant", text=f"I will use {secret} now")

    assert secret not in rec.path.read_text(encoding="utf-8")
    assert "[REDACTED]" in rec.path.read_text(encoding="utf-8")


def test_a_secret_at_the_cut_is_scrubbed_before_the_output_is_cut(tmp_path: Path) -> None:
    secret = "sk-test-0123456789abcdef"
    rec = recorder(tmp_path, scrubber=Scrubber([secret]), max_output_chars=MIN_OUTPUT_CHARS)

    call(rec, "x" * (MIN_OUTPUT_CHARS - 5) + secret + "tail")

    text = rec.path.read_text(encoding="utf-8")
    assert "sk-test" not in text and secret[:8] not in text


def test_output_is_capped_but_never_below_the_floor(tmp_path: Path) -> None:
    rec = recorder(tmp_path, max_output_chars=100)  # asks for less than the floor

    call(rec, "y" * (MIN_OUTPUT_CHARS + 500))

    (entry,) = read_trajectory(rec.path)
    assert entry.truncated and entry.output.startswith("y" * MIN_OUTPUT_CHARS)
    assert "truncated 500 more characters" in entry.output
    assert len(entry.output) < MIN_OUTPUT_CHARS + 100


def test_the_configured_cap_applies_to_big_outputs_and_arguments(tmp_path: Path) -> None:
    rec = recorder(tmp_path, max_output_chars=20_000)

    call(rec, "z" * 19_000, args={"content": "w" * 5_000})

    (entry,) = read_trajectory(rec.path)
    assert len(entry.output) == 19_000 and not entry.output.endswith("]")
    assert entry.truncated  # the argument was cut
    assert len(entry.args["content"]) < 2_100


def test_past_the_size_budget_outputs_are_cut_harder_then_recording_stops(tmp_path: Path) -> None:
    rec = recorder(tmp_path, max_mb=0.01)  # about 10 KB
    for _ in range(8):
        call(rec, "q" * 4_000)
    size = rec.path.stat().st_size

    entries = list(read_trajectory(rec.path))
    kinds = [entry.kind for entry in entries]
    outputs = [len(entry.output) for entry in entries if entry.kind == "tool"]
    assert outputs[0] == 4_000  # under budget: whole
    assert BUDGET_OUTPUT_CHARS < outputs[-1] < 1_100 and entries[-2].truncated
    assert kinds[-1] == "notice" and kinds.count("notice") == 2  # budget reached, then full
    assert call(rec) is None  # nothing more is written, and no seq is promised
    assert rec.path.stat().st_size == size


# -- a scripted run ------------------------------------------------------------------------------


def run_crew(ctx: RunContext, llm: ScriptedLLM, role: str = "Backend Engineer") -> None:
    agent = build_agent(ctx, llm, role=role, tools=build_tools(ctx, agent=role))
    with bind_run(ctx.run_id, ctx.events, ctx.trajectory), stage_scope("implement"):
        Crew(
            agents=[agent],
            tasks=[build_task(agent, f"Work as {role}.")],
            process=Process.sequential,
            verbose=False,
            tracing=False,
            memory=False,
            cache=False,
        ).kickoff()
    flush_bridge()


def entries_of(ctx: RunContext) -> list[TrajectoryEntry]:
    return list(read_trajectory(ctx.run_dir / "trajectory.jsonl"))


def test_a_scripted_run_records_outputs_messages_and_links_its_events(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    (ctx.workspace.root / "a.txt").write_text("hello from a\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            ToolCall("Read Project File", {"path": "a.txt"}),
            ToolCall("Read Project File", {"path": "missing.txt"}),
            "all done",
        ],
        native_tools=False,
    )

    run_crew(ctx, llm)

    entries = entries_of(ctx)
    tools = [entry for entry in entries if entry.kind == "tool"]
    assert [(t.tool, t.ok) for t in tools] == [
        ("Read Project File", True),
        ("Read Project File", False),
    ]
    assert "hello from a" in tools[0].output
    assert tools[1].output.startswith("ERROR: File not found")
    assert {t.teammate for t in tools} == {"Backend Engineer"}
    assert {entry.stage for entry in entries} == {"implement"}
    assistant = [e for e in entries if e.kind == "message" and e.role == "assistant"]
    final = [e for e in entries if e.kind == "message" and e.role == "final"]
    assert any("Action: Read Project File" in e.text for e in assistant)
    assert [e.text for e in final] == ["all done"]
    # every tool.call event names the trajectory entry it belongs to
    events = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "tool.call"]
    assert [e.data["trajectory_seq"] for e in events] == [t.seq for t in tools]


def test_an_agent_answer_comes_after_the_calls_it_made_when_traced(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    llm = ScriptedLLM([ToolCall("List Project Files", {}), "done"], native_tools=False)

    run_crew(ctx, llm)

    shown = [
        line.split("]")[0] + "]"
        for line in render_text(entries_of(ctx)).splitlines()
        if line.startswith("[")
    ]
    assert shown == ["[MESSAGE]", "[ACTION]", "[OBSERVATION]", "[MESSAGE]", "[MESSAGE]"]


def test_a_key_in_a_tool_output_is_scrubbed_from_the_whole_run_directory(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "fake-service-token-9f8e7d6c5b4a"
    monkeypatch.setenv("FAKE_SERVICE_TOKEN", secret)
    ctx = make_context()
    (ctx.workspace.root / "config.txt").write_text(f"token = {secret}\n", encoding="utf-8")
    llm = ScriptedLLM(
        [ToolCall("Read Project File", {"path": "config.txt"}), f"it says {secret}"],
        native_tools=False,
    )

    run_crew(ctx, llm)

    text = (ctx.run_dir / "trajectory.jsonl").read_text(encoding="utf-8")
    assert secret not in text and "[REDACTED]" in text
    assert len(entries_of(ctx)) >= 3


def test_the_size_settings_reach_the_recorder(tmp_path: Path) -> None:
    from engineering_team.tools import ProjectWorkspace

    settings = load_settings(overrides={"trajectory.max_output_chars": 12_000})
    ctx = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "p"))

    ctx.trajectory.tool_call(tool="t", args={}, output="e" * 13_000, ok=True, ms=1)

    (entry,) = entries_of(ctx)
    assert entry.truncated and entry.output.startswith("e" * 12_000)
    with pytest.raises(ValueError, match="max_output_chars"):
        load_settings(overrides={"trajectory.max_output_chars": 9_999})


# -- rendering and the command -------------------------------------------------------------------


def write_failed_run_trajectory(path: Path) -> None:
    """A small failed run: a read, a failing edit, a recovery, then a second stage."""

    tick = iter(range(100))
    rec = TrajectoryRecorder(path, clock=lambda: T0 + timedelta(seconds=next(tick)))

    def when(seconds: int) -> datetime:
        return T0 + timedelta(seconds=seconds)

    rec.message(role="assistant", text="I will read the module.", teammate="Backend Engineer",
                stage="implement", ts=when(1))  # fmt: skip
    rec.tool_call(tool="Read Project File", args={"path": "app.py"}, ok=True, ms=12,
                  output="def main():\n    return 1\n", teammate="Backend Engineer",
                  stage="implement", ts=when(2))  # fmt: skip
    rec.tool_call(tool="Edit Project File", args={"path": "app.py", "old_text": "return 2"},
                  ok=False, ms=3, output="ERROR: old_text was not found in app.py. Read it first.",
                  teammate="Backend Engineer", stage="implement", ts=when(3))  # fmt: skip
    rec.message(role="final", text="I could not apply the change.", teammate="Backend Engineer",
                stage="implement", ts=when(4))  # fmt: skip
    rec.tool_call(tool="Run Tests", args={}, ok=True, ms=800, output="1 failed",
                  teammate="Test Engineer", stage="verify", ts=when(5))  # fmt: skip


def test_trace_text_matches_the_golden_output(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    write_failed_run_trajectory(path)

    text = render_text(list(read_trajectory(path)))

    assert text == GOLDEN.read_text(encoding="utf-8")


def test_a_late_recorded_reply_is_shown_before_the_call_it_asked_for(tmp_path: Path) -> None:
    # CrewAI hands the model's reply to a handler thread, so it can be written after the tool
    # call it asked for; the trace orders by time.
    rec = recorder(tmp_path)
    call(rec, "out", tool="Read", stage="s", ts=T0 + timedelta(seconds=2))
    rec.message(role="assistant", text="let me read", stage="s", ts=T0 + timedelta(seconds=1))

    lines = [
        ln for ln in render_text(list(read_trajectory(rec.path))).splitlines() if ln[:1] == "["
    ]

    assert lines[0].startswith("[MESSAGE]") and lines[1].startswith("[ACTION]")


def invoke(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["--workspace-root", str(workspace_root()), *args])


@pytest.fixture
def failed_run(monkeypatch: pytest.MonkeyPatch) -> Path:
    code, _ = run_new(monkeypatch, runner=FakeRunner(fail={"plan": 99}))
    assert code == 1
    run_dir = RunStore(project()).run_dir(latest_run())
    (run_dir / "trajectory.jsonl").unlink(missing_ok=True)
    write_failed_run_trajectory(run_dir / "trajectory.jsonl")
    return run_dir


def test_trace_reads_a_failed_run_end_to_end(failed_run: Path) -> None:
    result = invoke("trace")

    assert result.exit_code == 0
    assert result.stdout == GOLDEN.read_text(encoding="utf-8")
    assert "[ERROR] old_text was not found in app.py. Read it first." in result.stdout


def test_trace_can_show_one_stage(failed_run: Path) -> None:
    result = invoke("trace", "--stage", "verify")

    assert result.exit_code == 0
    assert "== stage: verify ==" in result.stdout and "implement" not in result.stdout
    assert "Run Tests" in result.stdout


def test_trace_rejects_a_stage_the_run_never_had(failed_run: Path) -> None:
    result = invoke("trace", "--stage", "deploy")

    assert result.exit_code == 2
    assert "no stage 'deploy'" in result.output and "implement, verify" in result.output


def test_trace_json_lists_the_entries(failed_run: Path) -> None:
    for args in (("trace", "--format", "json"), ("--json", "trace")):
        data = json.loads(invoke(*args).stdout)

        assert data["recorded"] is True and data["run_id"] == failed_run.name
        assert [e["kind"] for e in data["entries"]] == [
            "message",
            "tool",
            "tool",
            "message",
            "tool",
        ]
        assert data["entries"][2]["ok"] is False


def test_trace_rejects_an_unknown_format(failed_run: Path) -> None:
    assert invoke("trace", "--format", "xml").exit_code == 2


def test_a_run_without_a_trajectory_says_it_was_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _ = run_new(monkeypatch)  # the scripted stage runner calls no tools: like a 0.2.0 run
    assert code == 0
    (RunStore(project()).run_dir(latest_run()) / "trajectory.jsonl").unlink(missing_ok=True)

    text = invoke("trace")
    data = json.loads(invoke("trace", "--format", "json").stdout)

    assert text.exit_code == 0 and "not recorded" in text.stdout
    assert data["recorded"] is False and data["entries"] == []


# -- the report ----------------------------------------------------------------------------------


def test_the_report_links_the_trace(tmp_path: Path) -> None:
    run_dir = run_dir_in(tmp_path)
    write_manifest(run_dir)
    write_failed_run_trajectory(run_dir / "trajectory.jsonl")

    report = build_report(run_dir)
    md = write_report(run_dir, "md").read_text(encoding="utf-8")
    html = write_report(run_dir, "html").read_text(encoding="utf-8")

    assert report.trace is not None and report.trace.entries == 5 and not report.trace.notices
    assert "## Trace" in md and f"engineering-team trace {run_dir.name}" in md
    assert 'href="trajectory.jsonl"' in html and 'id="trace"' in html


def test_the_report_says_when_the_size_budget_cut_outputs(tmp_path: Path) -> None:
    run_dir = run_dir_in(tmp_path)
    write_manifest(run_dir)
    rec = TrajectoryRecorder(run_dir / "trajectory.jsonl", max_mb=0.01)
    for _ in range(6):
        call(rec, "q" * 4_000)

    md = write_report(run_dir, "md").read_text(encoding="utf-8")
    html = write_report(run_dir, "html").read_text(encoding="utf-8")

    assert "size budget reached" in md and "later outputs are cut to 1000 characters" in md
    assert "size budget reached" in html


def test_a_run_without_a_trajectory_has_no_trace_section(tmp_path: Path) -> None:
    run_dir = run_dir_in(tmp_path)
    write_manifest(run_dir)

    assert build_report(run_dir).trace is None
    assert "## Trace" not in write_report(run_dir, "md").read_text(encoding="utf-8")
    assert 'id="trace"' not in write_report(run_dir, "html").read_text(encoding="utf-8")


# -- the web UI ----------------------------------------------------------------------------------


def test_a_tool_call_event_keeps_its_trajectory_link_in_the_card_trail() -> None:
    from engineering_team.contracts import Event
    from engineering_team.runtime.usage import UsageTracker
    from engineering_team.ui.eventlog import Digest, fold

    digest = Digest()
    data = {"tool": "Read", "ok": True, "duration": 0.1, "args": {}}
    for seq, extra in ((1, {"trajectory_seq": 7}), (2, {}), (3, {"trajectory_seq": "x"})):
        event = Event(seq=seq, ts=T0, run_id="r", type="tool.call", agent="dev", data=data | extra)
        fold(event, digest, UsageTracker())

    assert [record.trajectory_seq for record in digest.trail] == [7, None, None]


def test_the_ui_serves_one_trajectory_entry_to_the_drawer() -> None:
    pytest.importorskip("fastapi")
    from ui_helpers import API, make_client, start, wait_status

    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    run_dir = RunStore(project()).run_dir(run_id)
    rec = TrajectoryRecorder(run_dir / "trajectory.jsonl")
    call(rec, "the output", tool="Read Project File", args={"path": "a.txt"})

    found = client.get(f"{API}/runs/{run_id}/trajectory/1")
    missing = client.get(f"{API}/runs/{run_id}/trajectory/2")
    other = client.get(f"{API}/runs/no-such-run/trajectory/1")

    assert found.status_code == 200
    assert found.json()["output"] == "the output" and found.json()["args"] == {"path": "a.txt"}
    assert missing.status_code == 404 and "not recorded" in missing.json()["error"]
    assert other.status_code == 404
