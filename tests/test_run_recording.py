"""Run manifests, the CrewAI event bridge, and the full record a finished run leaves."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from crewai import Crew, Process

from engineering_team import main
from engineering_team.contracts import Event, RunManifest
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import FanoutSink, read_events
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, Turn, build_agent, build_task
from engineering_team.tools import ProjectWorkspace

MakeContext = Callable[..., RunContext]
REQUEST = "Build a tiny notes app."


def _events(ctx: RunContext) -> list[Event]:
    return list(read_events(ctx.run_dir / "events.jsonl"))


def _run_crew(ctx: RunContext, llm: ScriptedLLM, role: str = "Test Engineer") -> None:
    agent = build_agent(ctx, llm, role=role)
    Crew(
        agents=[agent],
        tasks=[build_task(agent, f"Work as {role}.")],
        process=Process.sequential,
        verbose=False,
        tracing=False,
        memory=False,
        cache=False,
    ).kickoff()


# -- the bridge -----------------------------------------------------------------------------


def test_a_scripted_crew_produces_the_expected_event_types(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [
            Turn(ToolCall("Write Project File", {"path": "a.txt", "content": "x"}), 100, 10),
            Turn("all done", 150, 20),
        ]
    )

    with bind_run(ctx.run_id, ctx.events):
        _run_crew(ctx, llm)
    flush_bridge()

    events = _events(ctx)
    types = [event.type for event in events]
    assert types[0] == "crew.started" and types[-1] == "crew.completed"
    for expected in (
        "task.started",
        "agent.started",
        "llm.call",
        "tool.call",  # emitted by our tools
        "tool.finished",  # emitted by CrewAI
        "agent.completed",
        "task.completed",
    ):
        assert expected in types, expected
    assert types.index("task.started") < types.index("agent.started") < types.index("llm.call")
    assert (
        types.index("agent.completed")
        < types.index("task.completed")
        < types.index("crew.completed")
    )
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert {event.run_id for event in events} == {ctx.run_id}


def test_llm_events_carry_token_usage_and_agents_are_attributed(make_context: MakeContext) -> None:
    ctx = make_context()
    llm = ScriptedLLM(
        [Turn(ToolCall("List Project Files", {}), 100, 10), Turn("done", 150, 20)],
    )

    with bind_run(ctx.run_id, ctx.events):
        _run_crew(ctx, llm, role="Backend Engineer")
    flush_bridge()

    calls = [event for event in _events(ctx) if event.type == "llm.call"]
    assert [call.data["usage"]["prompt_tokens"] for call in calls] == [100, 150]
    assert [call.data["usage"]["total_tokens"] for call in calls] == [110, 170]
    assert all(call.data["model"] == "scripted/fake" for call in calls)
    assert {call.agent for call in calls} == {"Backend Engineer"}
    completed = next(event for event in _events(ctx) if event.type == "task.completed")
    assert completed.agent == "Backend Engineer" and completed.data["output"] == "done"


def test_events_outside_a_bound_run_are_ignored(make_context: MakeContext) -> None:
    ctx = make_context()

    _run_crew(ctx, ScriptedLLM(["done"]))  # no bind_run
    flush_bridge()

    assert [e.type for e in _events(ctx)] == []


def test_binding_ends_with_its_scope(make_context: MakeContext) -> None:
    ctx = make_context()
    with bind_run(ctx.run_id, ctx.events):
        pass

    _run_crew(ctx, ScriptedLLM(["done"]))
    flush_bridge()

    assert _events(ctx) == []


def test_two_concurrent_runs_never_see_each_others_events(make_context: MakeContext) -> None:
    first, second = make_context("first"), make_context("second")
    barrier = threading.Barrier(2)

    def work(ctx: RunContext, role: str) -> None:
        llm = ScriptedLLM(
            [
                ToolCall("Write Project File", {"path": f"{role}.txt", "content": role}),
                f"{role} finished",
            ]
        )
        with bind_run(ctx.run_id, ctx.events):
            barrier.wait(timeout=30)  # both runs are bound before either starts
            _run_crew(ctx, llm, role=role)

    threads = [
        threading.Thread(target=work, args=(first, "Alpha")),
        threading.Thread(target=work, args=(second, "Beta")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    flush_bridge()

    for ctx, mine, other in ((first, "Alpha", "Beta"), (second, "Beta", "Alpha")):
        events = _events(ctx)
        assert {event.run_id for event in events} == {ctx.run_id}
        agents = {event.agent for event in events if event.agent}
        assert agents == {mine}, (ctx.run_id, agents)
        assert other not in json.dumps([event.model_dump(mode="json") for event in events])
        assert (
            f"{mine} finished"
            in next(e for e in events if e.type == "task.completed").data["output"]
        )


def test_bridged_events_are_scrubbed_like_any_other(make_context: MakeContext, monkeypatch) -> None:
    secret = "sk-abcdefghijklmnop1234"
    monkeypatch.setenv("SERVICE_API_KEY", secret)
    ctx = make_context()

    with bind_run(ctx.run_id, ctx.events):
        _run_crew(ctx, ScriptedLLM([f"the key is {secret}"]))
    flush_bridge()

    raw = (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in raw and "[REDACTED]" in raw


# -- the recorder ---------------------------------------------------------------------------


def _recorder(ctx: RunContext) -> RunRecorder:
    return RunRecorder.begin(ctx, mode="build", request=REQUEST)


def test_beginning_a_run_writes_its_manifest_request_and_settings(
    make_context: MakeContext,
) -> None:
    ctx = make_context()

    recorder = _recorder(ctx)

    manifest = RunManifest.model_validate_json((ctx.run_dir / "manifest.json").read_text("utf-8"))
    assert manifest.run_id == ctx.run_id and manifest.status == "pending"
    assert manifest.project_name == ctx.settings.project_name and manifest.mode == "build"
    assert manifest.request_hash == hashlib.sha256(REQUEST.encode()).hexdigest()
    assert len(manifest.settings_hash) == 64
    assert set(manifest.versions) == {"engineering_team", "crewai", "python"}
    assert manifest.strategy == "hierarchical" and manifest.recipe is None
    assert (ctx.run_dir / "request.md").read_text(encoding="utf-8") == REQUEST + "\n"
    assert json.loads((ctx.run_dir / "settings.json").read_text("utf-8"))["project_name"]
    assert recorder.manifest == manifest


def test_the_settings_hash_changes_with_the_settings(tmp_path: Path) -> None:
    def hash_for(**overrides: str) -> str:
        workspace = ProjectWorkspace.create(
            tmp_path / f"w{len(overrides)}{overrides.get('provider', '')}"
        )
        ctx = RunContext.create(load_settings(overrides=overrides), workspace)
        return RunRecorder.begin(ctx, request=REQUEST).manifest.settings_hash

    assert hash_for() == hash_for()
    assert hash_for() != hash_for(provider="anthropic")


def test_a_successful_run_goes_pending_running_succeeded(make_context: MakeContext) -> None:
    ctx = make_context()
    recorder = _recorder(ctx)

    with recorder.running():
        assert recorder.manifest.status == "running"

    manifest = recorder.manifest
    assert manifest.status == "succeeded" and manifest.finished is not None
    types = [event.type for event in _events(ctx)]
    assert types == ["run.started", "run.finished"]
    finished = _events(ctx)[-1].data
    assert finished["status"] == "succeeded" and finished["summary"]["status"] == "succeeded"


def test_a_failing_run_is_recorded_as_failed_with_its_error(make_context: MakeContext) -> None:
    ctx = make_context()
    recorder = _recorder(ctx)

    with pytest.raises(RuntimeError, match="model unavailable"), recorder.running():
        raise RuntimeError("model unavailable")

    assert recorder.manifest.status == "failed"
    finished = _events(ctx)[-1].data
    assert finished["status"] == "failed" and finished["error"] == "RuntimeError: model unavailable"


def test_ctrl_c_is_recorded_as_interrupted(make_context: MakeContext) -> None:
    ctx = make_context()
    recorder = _recorder(ctx)

    with pytest.raises(KeyboardInterrupt), recorder.running():
        raise KeyboardInterrupt

    assert recorder.manifest.status == "interrupted"


@pytest.mark.parametrize("raises", [False, True])
def test_a_cancelled_run_is_recorded_as_cancelled(make_context: MakeContext, raises: bool) -> None:
    ctx = make_context()
    recorder = _recorder(ctx)

    def body() -> None:
        with recorder.running():
            ctx.cancel_event.set()
            if raises:
                raise RuntimeError("stopped midway")

    if raises:
        with pytest.raises(RuntimeError):
            body()
    else:
        body()

    assert recorder.manifest.status == "cancelled"


def test_a_finished_fake_run_is_fully_described_by_its_manifest_and_event_log(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    recorder = _recorder(ctx)
    llm = ScriptedLLM(
        [
            Turn(
                ToolCall("Write Project File", {"path": "app.py", "content": "print('hi')\n"}),
                120,
                12,
            ),
            Turn(ToolCall("Run Project Command", {"command": "python app.py"}), 180, 14),
            Turn("Wrote and ran app.py.", 240, 16),
        ]
    )

    with recorder.running():
        _run_crew(ctx, llm)

    manifest = RunManifest.model_validate_json((ctx.run_dir / "manifest.json").read_text("utf-8"))
    events = _events(ctx)
    assert manifest.status == "succeeded" and manifest.finished is not None
    assert manifest.finished >= manifest.created
    # The log starts and ends with the run itself, with the whole crew inside.
    assert events[0].type == "run.started" and events[-1].type == "run.finished"
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    inner = [e.type for e in events[1:-1]]
    assert inner[0] == "crew.started" and inner[-1] == "crew.completed"
    # Every tool the agent used and every model call is on record, with usage.
    assert [e.data["tool"] for e in events if e.type == "tool.call"] == [
        "Write Project File",
        "Run Project Command",
    ]
    usage = [e.data["usage"]["total_tokens"] for e in events if e.type == "llm.call"]
    assert usage == [132, 194, 256]
    assert sum(usage) == next(e for e in events if e.type == "crew.completed").data["total_tokens"]
    # And the files the manifest points at exist next to it.
    assert (ctx.run_dir / "request.md").is_file() and (ctx.run_dir / "commands" / "1.log").is_file()
    assert RunStore(ctx.workspace.root).latest() == manifest


# -- through the CLI ------------------------------------------------------------------------


class _FakeTeam:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    def crew(self):  # type: ignore[no-untyped-def]
        ctx = self.ctx

        class _Crew:
            def kickoff(self, inputs: dict[str, str]) -> None:
                ctx.events.emit("probe", requirements=inputs["requirements"])

        return _Crew()


def _only_run(root: Path) -> tuple[RunManifest, Path]:
    runs = RunStore(root / "workspace" / "demo").list_runs()
    assert len(runs) == 1
    return runs[0], root / "workspace" / "demo" / ".engineering-team" / "runs" / runs[0].run_id


@pytest.mark.usefixtures("crew_strategy")
def test_a_cli_run_leaves_a_manifest_and_event_log(monkeypatch) -> None:
    monkeypatch.setattr("engineering_team.pipeline.strategies.EngineeringTeam", _FakeTeam)

    assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 0

    manifest, run_dir = _only_run(Path.cwd())
    assert manifest.status == "succeeded" and manifest.mode == "build"
    assert (run_dir / "request.md").read_text(encoding="utf-8").strip() == REQUEST
    types = [e.type for e in read_events(run_dir / "events.jsonl")]
    assert types == ["run.started", "probe", "run.finished"]
    legacy = Path.cwd() / "workspace" / "demo" / ".engineering-team"
    assert not (legacy / "request.md").exists() and not (legacy / "run.json").exists()


@pytest.mark.usefixtures("crew_strategy")
def test_a_failing_cli_run_is_recorded_as_failed(monkeypatch) -> None:
    class Exploding:
        def __init__(self, ctx: RunContext) -> None:
            pass

        def crew(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    monkeypatch.setattr("engineering_team.pipeline.strategies.EngineeringTeam", Exploding)

    assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 1

    manifest, run_dir = _only_run(Path.cwd())
    assert manifest.status == "failed"
    assert list(read_events(run_dir / "events.jsonl"))[-1].data["error"] == "RuntimeError: boom"


def test_prepare_only_is_recorded_as_a_prepare_run(monkeypatch) -> None:
    assert main.run(["--request", REQUEST, "--project-name", "demo", "--prepare-only"]) == 0

    manifest, _ = _only_run(Path.cwd())
    assert manifest.mode == "prepare" and manifest.status == "succeeded"


@pytest.mark.usefixtures("crew_strategy")
def test_every_run_of_a_workspace_is_listed_in_order(monkeypatch) -> None:
    monkeypatch.setattr("engineering_team.pipeline.strategies.EngineeringTeam", _FakeTeam)

    for _ in range(3):
        assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 0

    runs = RunStore(Path.cwd() / "workspace" / "demo").list_runs()
    assert len(runs) == 3 and [r.run_id for r in runs] == sorted(r.run_id for r in runs)
    assert all(run.status == "succeeded" for run in runs)


def test_jsonl_sink_default_matches_the_run_directory(make_context: MakeContext) -> None:
    ctx = make_context()

    ctx.events.emit("probe")

    assert [event.type for event in _events(ctx)] == ["probe"]
    assert isinstance(ctx.events, FanoutSink) and (ctx.run_dir / "events.jsonl").is_file()
