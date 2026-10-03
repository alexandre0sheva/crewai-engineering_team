"""Smaller pieces of the pipeline: ordering, controller stages, run lifecycle, settings."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pipeline_fakes import REQUEST, FakeRunner, settings_for

from engineering_team.contracts import Plan, RunManifest, StageRecord, WorkPackage
from engineering_team.pipeline.actions import CONTROLLER_ACTIONS, register_action
from engineering_team.pipeline.packages import PlanError, order_packages, plan_problems
from engineering_team.pipeline.recipes import parse_recipe
from engineering_team.pipeline.state import PipelineState, RunBundle
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import EVENTS_FILENAME, InvalidTransition, RunStore
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import SettingsError, load_settings

MakeContext = Callable[..., RunContext]


def package(id_: str, *deps: str) -> WorkPackage:
    return WorkPackage(
        id=id_, title=id_, role="backend", depends_on=list(deps), owned_paths=[f"{id_.strip()}/**"]
    )


# -- work packages ---------------------------------------------------------------------------


def test_packages_run_layer_by_layer_with_plan_order_inside_a_layer() -> None:
    plan = Plan(work_packages=[package("C", "B"), package("A"), package("B", "A"), package("D")])

    assert [p.id for p in order_packages(plan)] == ["A", "D", "B", "C"]
    assert plan_problems(plan) == []


def test_plan_problems_lists_everything_wrong() -> None:
    plan = Plan(work_packages=[package("A", "A"), package("A"), package("B", "Z"), package(" ")])

    problems = plan_problems(plan)

    assert "A depends on itself" in problems
    assert "work package id 'A' is used twice" in problems
    assert "B depends on unknown package 'Z'" in problems
    assert "a work package has no id" in problems


def test_a_dependency_cycle_is_named() -> None:
    plan = Plan(work_packages=[package("A", "B"), package("B", "A"), package("C")])

    with pytest.raises(PlanError, match="A, B depend on each other in a cycle"):
        order_packages(plan)
    assert any("cycle" in problem for problem in plan_problems(plan))


# -- controller stages -----------------------------------------------------------------------


def test_a_controller_stage_runs_a_registered_action_without_a_model(
    monkeypatch: pytest.MonkeyPatch, make_context: MakeContext, tmp_path: Path
) -> None:
    from engineering_team import main
    from engineering_team.pipeline import strategies
    from engineering_team.pipeline.runner import execute_run

    seen: list[str] = []

    @register_action("note-the-tree")
    def note(ctx: RunContext, state: PipelineState) -> str:
        seen.append(ctx.run_id)
        ctx.workspace.write_file("docs/tree.txt", "tree " * 20)
        return "noted"

    try:
        recipe = parse_recipe(
            """
name: with-controller
stages:
  - {name: plan, teammates: [solution_architect], outputs: [plan]}
  - name: note
    kind: controller
    action: note-the-tree
    outputs: ["file:docs/tree.txt"]
    verification_policy: artifacts
""",
            source="test",
        )
        runner = FakeRunner()
        monkeypatch.setattr(strategies, "CrewStageRunner", lambda: runner)
        settings = settings_for(tmp_path)
        prepared = main._open_run(settings, mode="build", requirements=REQUEST)
        try:
            result = execute_run(
                prepared.ctx, RunBundle(REQUEST), strategy=prepared.strategy, recipe=recipe
            )
        finally:
            prepared.lock.release()
    finally:
        CONTROLLER_ACTIONS.pop("note-the-tree", None)

    assert result.status == "succeeded" and seen == [prepared.ctx.run_id]
    assert [call[0] for call in runner.calls] == ["plan"]  # the controller stage called no agent
    state = PipelineState.load(prepared.ctx.run_dir)
    assert state is not None and state.summaries["note"] == "noted"


def test_an_unregistered_controller_action_fails_the_stage_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from engineering_team import main
    from engineering_team.pipeline import strategies
    from engineering_team.pipeline.runner import execute_run

    recipe = parse_recipe(
        "name: r\nstages:\n  - {name: only, kind: controller, action: nope}\n", source="test"
    )
    monkeypatch.setattr(strategies, "CrewStageRunner", FakeRunner)
    prepared = main._open_run(settings_for(tmp_path), mode="build", requirements=REQUEST)
    try:
        result = execute_run(
            prepared.ctx, RunBundle(REQUEST), strategy=prepared.strategy, recipe=recipe
        )
    finally:
        prepared.lock.release()

    assert result.status == "failed"
    assert "Unknown controller action 'nope'" in result.error


# -- run lifecycle ---------------------------------------------------------------------------


def _manifest(run_id: str = "20260101-000000-aaaaaa") -> RunManifest:
    return RunManifest(run_id=run_id, project_name="p", request_hash="h")


@pytest.mark.parametrize("end", ["interrupted", "failed", "cancelled"])
def test_a_run_that_did_not_succeed_can_be_reopened(tmp_path: Path, end: str) -> None:
    store = RunStore(tmp_path)
    store.create(_manifest())
    store.set_status("20260101-000000-aaaaaa", "running")
    store.set_status("20260101-000000-aaaaaa", end)  # type: ignore[arg-type]

    reopened = store.set_status("20260101-000000-aaaaaa", "running")

    assert reopened.status == "running" and reopened.finished is None and reopened.summary is None


def test_a_succeeded_run_stays_final(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.create(_manifest())
    store.set_status("20260101-000000-aaaaaa", "running")
    store.set_status("20260101-000000-aaaaaa", "succeeded")

    with pytest.raises(InvalidTransition):
        store.set_status("20260101-000000-aaaaaa", "running")


def test_reopen_turns_a_stale_running_run_into_an_interrupted_one(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    RunRecorder.begin(ctx, request=REQUEST)
    store = RunStore(ctx.workspace.root)
    store.set_status(ctx.run_id, "running")  # the process died before it could finish
    store.record_stage(ctx.run_id, StageRecord(name="plan", status="running", attempts=1))

    recorder = RunRecorder.reopen(ctx)

    manifest = recorder.manifest
    assert manifest.status == "interrupted" and manifest.resumes == 1
    assert manifest.stages[0].status == "interrupted"
    with recorder.running():
        pass
    assert recorder.manifest.status == "succeeded"
    started = [e for e in read_events(ctx.run_dir / EVENTS_FILENAME) if e.type == "run.started"]
    assert started[-1].data["resumed"] == 1


def test_fail_ends_the_run_failed_without_raising(make_context: MakeContext) -> None:
    ctx = make_context()
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    with recorder.running() as run:
        run.fail("a stage failed")

    manifest = recorder.manifest
    assert manifest.status == "failed"
    finished = [e for e in read_events(ctx.run_dir / EVENTS_FILENAME) if e.type == "run.finished"]
    assert finished[-1].data["error"] == "a stage failed"


def test_a_cancelled_stage_is_recorded_cancelled_and_a_skipped_one_keeps_the_revision(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    with recorder.running():
        recorder.skip_stage("optional", "skipped: nothing to do")
        with pytest.raises(RunCancelled), recorder.stage("work"):
            raise RunCancelled("stop")

    records = {record.name: record for record in recorder.manifest.stages}
    assert records["work"].status == "cancelled" and records["work"].detail == "stop"
    assert records["work"].revision and records["work"].revision_start
    skipped = records["optional"]
    assert skipped.status == "skipped" and skipped.revision == skipped.revision_start
    assert skipped.detail == "skipped: nothing to do"


def test_a_failed_stage_records_why(make_context: MakeContext) -> None:
    ctx = make_context()
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    with recorder.running(), pytest.raises(ValueError), recorder.stage("work"):
        raise ValueError("bad input")

    record = recorder.manifest.stages[0]
    assert record.status == "failed" and record.detail == "ValueError: bad input"


def test_a_resumed_context_continues_the_usage_and_the_event_log(
    make_context: MakeContext,
) -> None:
    first = make_context()
    first.events.emit(
        "llm.call",
        agent="a",
        model="scripted/fake",
        usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    first.events.emit("tool.call", tool="x", ok=True)
    seq = len(list(read_events(first.run_dir / EVENTS_FILENAME)))

    second = RunContext.create(first.settings, first.workspace, run_id=first.run_id, resume=True)
    second.events.emit("probe")

    totals = second.usage.totals()
    assert totals.prompt_tokens == 7 and totals.completion_tokens == 3 and totals.calls == 1
    assert second.usage.tool_calls == 1
    events = list(read_events(second.run_dir / EVENTS_FILENAME))
    assert [e.seq for e in events] == list(range(1, seq + 2))


# -- settings --------------------------------------------------------------------------------


def test_strategy_defaults_to_hierarchical_and_is_validated() -> None:
    assert load_settings().strategy == "hierarchical"
    assert load_settings(env={"ENGINEERING_STRATEGY": "pipeline"}).strategy == "pipeline"
    assert load_settings(overrides={"strategy": "single"}).strategy == "single"
    with pytest.raises(SettingsError, match="strategy"):
        load_settings(env={"ENGINEERING_STRATEGY": "swarm"})


def test_config_show_reports_the_strategy_and_where_it_came_from() -> None:
    rows = {
        row.key: row for row in load_settings(env={"ENGINEERING_STRATEGY": "pipeline"}).describe()
    }

    assert (
        rows["strategy"].value == "pipeline" and "ENGINEERING_STRATEGY" in rows["strategy"].source
    )


def test_the_state_file_round_trips_and_tolerates_a_missing_or_torn_file(tmp_path: Path) -> None:
    assert PipelineState.load(tmp_path) is None
    state = PipelineState(recipe="new", error="x")
    state.save(tmp_path)
    assert "id" not in json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))

    loaded = PipelineState.load(tmp_path)
    assert loaded is not None and loaded.recipe == "new" and loaded.error == "x"

    (tmp_path / "pipeline.json").write_text("{ torn", encoding="utf-8")
    assert PipelineState.load(tmp_path) is None
