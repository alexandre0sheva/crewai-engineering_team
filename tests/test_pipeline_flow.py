"""The staged pipeline end to end, driven through the CLI with a scripted stage runner."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pipeline_fakes import PLAN, REQUEST, STAGES, FakeRunner

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.contracts import Plan, RunManifest, StageRecord
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import STATE_FILENAME, PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.snapshot import workspace_revision
from engineering_team.tools.workspace import ProjectWorkspace

ROOT = "ws"


def use_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> FakeRunner:
    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: runner)
    return runner


def project(name: str = "demo") -> Path:
    return Path.cwd() / ROOT / name


def start(*extra: str, name: str = "demo", request: str = REQUEST) -> int:
    return main.run(
        [
            "--request",
            request,
            "--project-name",
            name,
            "--workspace-root",
            str(Path.cwd() / ROOT),
            "--strategy",
            "pipeline",
            *extra,
        ]
    )


def resume(run_id: str, *extra: str, name: str = "demo") -> int:
    return main.run(
        [
            "resume",
            run_id,
            "--project-name",
            name,
            "--workspace-root",
            str(Path.cwd() / ROOT),
            *extra,
        ]
    )


def runs(name: str = "demo") -> list[RunManifest]:
    return RunStore(project(name)).list_runs()


def only_run(name: str = "demo") -> RunManifest:
    (manifest,) = runs(name)
    return manifest


def run_dir(manifest: RunManifest, name: str = "demo") -> Path:
    return RunStore(project(name)).run_dir(manifest.run_id)


def tree(name: str = "demo") -> str:
    return workspace_revision(ProjectWorkspace.create(project(name)))


def stage_statuses(manifest: RunManifest) -> dict[str, str]:
    return {record.name: record.status for record in manifest.stages}


@pytest.fixture
def reference_tree(monkeypatch: pytest.MonkeyPatch) -> str:
    """The workspace an uninterrupted scripted run leaves behind."""

    use_runner(monkeypatch, FakeRunner())
    assert start(name="reference") == 0
    return tree("reference")


# -- a straight run ----------------------------------------------------------------------------


def test_a_scripted_six_stage_run_goes_through_every_stage_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    assert runner.calls == [
        ("spec", None),
        ("plan", None),
        ("foundation", None),
        ("implement", "WP-1"),
        ("implement", "WP-2"),
        ("integrate", None),
        ("verify", None),
        ("release", None),
    ]
    manifest = only_run()
    assert manifest.status == "succeeded" and manifest.strategy == "pipeline"
    assert manifest.recipe == "new" and manifest.resumes == 0
    assert [record.name for record in manifest.stages] == list(STAGES)
    assert {record.status for record in manifest.stages} == {"succeeded"}
    # Each stage started from the tree the previous one left: that chain is what resume trusts.
    records = manifest.stages
    assert all(a.revision == b.revision_start for a, b in zip(records, records[1:], strict=False))
    assert all(record.revision for record in records)
    for path in ("docs/architecture.md", "README.md", "src/wp-1.py", "docs/release-report.md"):
        assert (project() / path).is_file(), path


def test_the_run_state_holds_the_contracts_the_stages_handed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())
    start()

    state = PipelineState.load(run_dir(only_run()))

    assert state is not None
    assert state.spec is not None and state.spec.title == "Notes CLI"
    assert state.plan is not None and [p.id for p in state.plan.work_packages] == ["WP-1", "WP-2"]
    assert {pid: p.status for pid, p in state.packages.items()} == {
        "WP-1": "succeeded",
        "WP-2": "succeeded",
    }
    assert state.recipe == "new" and state.recipe_digest and state.request_hash
    assert state.summaries["spec"] == "spec done"


def test_stages_get_a_board_card_and_the_plan_adds_work_package_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, dict[str, str]] = {}

    def snapshot(request: StageRequest) -> None:
        board = request.ctx.board
        seen[f"{request.stage.name}/{request.package.id if request.package else ''}"] = {
            card.title: card.status for card in board.cards()
        }

    use_runner(monkeypatch, FakeRunner(on_call=snapshot))
    start()

    at_plan = seen["plan/"]
    assert at_plan == {
        "Spec": "done",
        "Plan": "in_progress",
        "Foundation": "backlog",
        "Implement": "backlog",
        "Integrate": "backlog",
        "Verify": "backlog",
        "Release": "backlog",
    }
    at_wp2 = seen["implement/WP-2"]
    assert at_wp2["WP-1: Storage"] == "done" and at_wp2["WP-2: CLI"] == "in_progress"
    assert at_wp2["Implement"] == "in_progress"

    board = BoardStore(run_dir(only_run()))
    stages = board.cards(kind="stage")
    packages = board.cards(kind="work_package")
    assert [card.status for card in stages] == ["done"] * 7
    assert [card.status for card in packages] == ["done", "done"]
    implement = next(card for card in stages if card.stage == "implement")
    assert {card.parent_id for card in packages} == {implement.id}
    assert packages[1].depends_on == [packages[0].id]
    assert packages[0].owned_paths == ["src/storage/**"] and packages[0].criteria_ids == ["AC-1"]
    # The controller, not an agent, moved every card to done, with the stage as the evidence.
    assert all(card.history[-1].actor == "controller" for card in stages + packages)
    assert board.progress().overall_percent == 100.0


def test_a_plan_without_work_packages_skips_the_implement_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(plan=Plan(stack="shell")))

    assert start() == 0

    assert [stage for stage, _ in runner.calls] == [
        "spec",
        "plan",
        "foundation",
        "verify",
        "release",
    ]
    manifest = only_run()
    implement = next(record for record in manifest.stages if record.name == "implement")
    assert implement.status == "skipped" and "no_work_packages" in implement.detail
    board = BoardStore(run_dir(manifest))
    card = next(card for card in board.cards(kind="stage") if card.stage == "implement")
    assert card.status == "cancelled"  # skipped cards leave the totals
    assert board.progress().overall_percent == 100.0
    types = [event.type for event in read_events(run_dir(manifest) / "events.jsonl")]
    assert "stage.skipped" in types


def test_user_notes_reach_each_teammate_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def note_during_spec(request: StageRequest) -> None:
        if request.stage.name == "spec":
            request.ctx.board.add_user_note("Prefer sqlite over files.")

    runner = use_runner(monkeypatch, FakeRunner(on_call=note_during_spec))
    start()

    steering = {
        (stage, package): request.steering
        for (stage, package), request in zip(runner.calls, runner.requests, strict=True)
    }
    note = "User note: Prefer sqlite over files."
    assert steering[("spec", None)] == ""  # it arrived while the architect was working
    # The first stage each teammate works on after the note carries it, and no later one does.
    assert [call for call, text in steering.items() if text == note] == [
        ("plan", None),  # solution_architect
        ("foundation", None),  # backend_engineer
        ("verify", None),  # quality_engineer
    ]
    assert all(text in ("", note) for text in steering.values())


def test_a_paused_run_waits_at_the_stage_boundary_until_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamps: dict[str, float] = {}

    def pause_after_spec(request: StageRequest) -> None:
        stamps[request.stage.name] = time.monotonic()
        if request.stage.name == "spec":
            request.ctx.board.pause()
            threading.Timer(0.5, request.ctx.board.unpause).start()

    use_runner(monkeypatch, FakeRunner(on_call=pause_after_spec))

    assert start() == 0

    assert stamps["plan"] - stamps["spec"] >= 0.4


# -- failure and resume --------------------------------------------------------------------------

FAIL_POINTS = ["spec", "plan", "foundation", "implement:WP-2", "integrate", "verify", "release"]


def _expected_rerun(point: str) -> list[tuple[str, str | None]]:
    full: list[tuple[str, str | None]] = [
        ("spec", None),
        ("plan", None),
        ("foundation", None),
        ("implement", "WP-1"),
        ("implement", "WP-2"),
        ("integrate", None),
        ("verify", None),
        ("release", None),
    ]
    stage, _, package = point.partition(":")
    first = full.index((stage, package or None))
    return full[first:]


@pytest.mark.parametrize("point", FAIL_POINTS)
def test_a_run_that_fails_at_any_stage_resumes_without_redoing_finished_work(
    monkeypatch: pytest.MonkeyPatch, reference_tree: str, point: str
) -> None:
    # Two failures use up the stage's automatic retry, so the run really fails.
    use_runner(monkeypatch, FakeRunner(fail={point: 2}))
    assert start() == 1
    failed = only_run()
    assert failed.status == "failed"
    stage = point.partition(":")[0]
    assert stage_statuses(failed)[stage] == "failed"

    resumed_runner = use_runner(monkeypatch, FakeRunner())
    assert resume(failed.run_id) == 0

    assert resumed_runner.calls == _expected_rerun(point)
    assert tree() == reference_tree
    final = only_run()
    assert final.run_id == failed.run_id and final.status == "succeeded" and final.resumes == 1
    assert {record.status for record in final.stages} == {"succeeded"}
    # The agent that continues is told to inspect the workspace and not redo finished work.
    assert "do not redo" in resumed_runner.requests[0].note.lower() or point == "release"
    board = BoardStore(run_dir(final))
    assert [card.status for card in board.cards(kind="stage")] == ["done"] * 7


@pytest.mark.parametrize("point", FAIL_POINTS)
def test_a_run_cancelled_after_any_stage_resumes_to_the_same_final_state(
    monkeypatch: pytest.MonkeyPatch, reference_tree: str, point: str
) -> None:
    stage = point.partition(":")[0]
    use_runner(monkeypatch, FakeRunner(cancel_at=stage))
    assert start() == 130
    cancelled = only_run()
    assert cancelled.status == "cancelled"
    assert stage_statuses(cancelled)[stage] == "cancelled"

    runner = use_runner(monkeypatch, FakeRunner())
    assert resume(cancelled.run_id) == 0

    done_before = list(STAGES[: STAGES.index(stage)])
    assert not [call for call in runner.calls if call[0] in done_before]
    assert runner.calls[0][0] == stage
    assert tree() == reference_tree
    assert only_run().status == "succeeded"


def test_a_package_finished_before_the_crash_is_not_run_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"implement:WP-2": 2}))
    start()
    state = PipelineState.load(run_dir(only_run()))
    assert state is not None
    assert state.packages["WP-1"].status == "succeeded"
    assert (
        state.packages["WP-2"].status == "failed"
        and "scripted failure" in state.packages["WP-2"].error
    )

    runner = use_runner(monkeypatch, FakeRunner())
    resume(only_run().run_id)

    assert ("implement", "WP-1") not in runner.calls
    assert (
        "previous attempt failed" in runner.requests[runner.calls.index(("implement", "WP-2"))].note
    )


def test_resuming_twice_after_two_failures_still_never_repeats_a_finished_stage(
    monkeypatch: pytest.MonkeyPatch, reference_tree: str
) -> None:
    all_calls: list[tuple[str, str | None]] = []
    first = use_runner(monkeypatch, FakeRunner(fail={"verify": 2}))
    start()
    all_calls += first.calls
    run_id = only_run().run_id
    second = use_runner(monkeypatch, FakeRunner(fail={"verify": 2}))
    assert resume(run_id) == 1
    all_calls += second.calls
    third = use_runner(monkeypatch, FakeRunner())
    assert resume(run_id) == 0
    all_calls += third.calls

    for finished in ("spec", "plan", "foundation", "implement"):
        assert len([c for c in all_calls if c[0] == finished]) == (
            2 if finished == "implement" else 1
        )
    assert tree() == reference_tree
    assert only_run().resumes == 2 and only_run().status == "succeeded"


def test_resuming_a_run_whose_every_stage_finished_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"release": 2}))
    start()
    run_id = only_run().run_id
    store = RunStore(project())
    # As if the process died after the last stage finished but before the run was closed:
    # the stage record says succeeded and the workspace holds its output.

    def finish_release(manifest: RunManifest) -> None:
        for record in manifest.stages:
            if record.name == "release":
                record.status, record.revision = (
                    "succeeded",
                    workspace_revision(ProjectWorkspace.create(project())),
                )

    store.update(run_id, finish_release)
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(run_id) == 0

    assert runner.calls == []
    assert only_run().status == "succeeded"


def test_a_run_left_running_by_a_killed_process_can_be_resumed(
    monkeypatch: pytest.MonkeyPatch, reference_tree: str
) -> None:
    use_runner(monkeypatch, FakeRunner(cancel_at="verify"))
    start()
    run_id = only_run().run_id

    def as_if_killed(manifest: RunManifest) -> None:
        manifest.status = "running"
        manifest.finished = None
        for record in manifest.stages:
            if record.name == "verify":
                record.status = "running"

    RunStore(project()).update(run_id, as_if_killed)
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(run_id) == 0

    assert runner.calls[0] == ("verify", None)
    assert tree() == reference_tree
    assert only_run().status == "succeeded"


def test_a_stage_whose_files_were_edited_after_it_finished_runs_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"release": 2}))
    start()
    run_id = only_run().run_id

    def release_never_started(manifest: RunManifest) -> None:
        for index, record in enumerate(manifest.stages):
            if record.name == "release":
                manifest.stages[index] = StageRecord(name="release")

    RunStore(project()).update(run_id, release_never_started)
    (project() / "README.md").write_text("# edited by hand\n" + "x " * 60, encoding="utf-8")
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(run_id) == 0

    # Nothing started after "verify", so the tree it left must still be there. It is not, so
    # "verify" runs again (with the inspect-first note) and so does everything after it.
    assert [call[0] for call in runner.calls] == ["verify", "release"]
    assert "do not redo" in runner.requests[0].note
    plans = [
        e for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == "resume.plan"
    ]
    stages = plans[-1].data["stages"]
    assert stages["verify"]["action"] == "rerun" and stages["foundation"]["action"] == "reuse"


# -- changed request, and runs that cannot be resumed --------------------------------------------


def test_resuming_with_a_changed_request_starts_a_new_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"foundation": 2}))
    start()
    old = only_run()
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(old.run_id, "--request", "Build something completely different.") == 0

    assert "request changed" in capsys.readouterr().err
    assert len(runs()) == 2
    untouched = RunStore(project()).load(old.run_id)
    assert untouched.status == "failed" and untouched.resumes == 0
    new = next(run for run in runs() if run.run_id != old.run_id)
    assert new.status == "succeeded" and new.strategy == "pipeline"
    assert runner.calls[0] == ("spec", None)  # nothing from the old run was reused
    assert (
        (run_dir(new) / "request.md").read_text(encoding="utf-8").strip().startswith("Build some")
    )


def test_resuming_with_the_same_request_continues_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"foundation": 2}))
    start()
    old = only_run()
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(old.run_id, "--request", f"  {REQUEST}\n") == 0

    assert len(runs()) == 1 and runner.calls[0] == ("foundation", None)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("request", "was modified after the run started"),
        ("recipe", "changed since"),
        ("unknown", "No run"),
    ],
)
def test_a_run_that_cannot_be_resumed_is_a_one_line_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tamper: str,
    message: str,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"foundation": 2}))
    start()
    run_id = only_run().run_id
    directory = run_dir(only_run())
    if tamper == "request":
        (directory / "request.md").write_text("something else\n", encoding="utf-8")
    elif tamper == "recipe":
        data = json.loads((directory / STATE_FILENAME).read_text(encoding="utf-8"))
        data["recipe_digest"] = "0" * 16
        (directory / STATE_FILENAME).write_text(json.dumps(data), encoding="utf-8")
    else:
        run_id = "20200101-000000-abcdef"

    assert resume(run_id) == 2

    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err
    assert only_run().status == "failed"


def test_a_succeeded_run_has_nothing_to_resume(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner())
    start()
    run_id = only_run().run_id

    assert resume(run_id) == 2

    assert "already succeeded" in capsys.readouterr().err
    assert only_run().resumes == 0


def test_a_hierarchical_run_cannot_be_resumed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Team:
        def __init__(self, ctx: object) -> None:
            pass

        def crew(self) -> object:
            raise RuntimeError("boom")

    monkeypatch.setattr(strategies, "EngineeringTeam", Team)
    assert start("--strategy", "hierarchical") == 1
    run_id = only_run().run_id

    assert resume(run_id) == 2

    assert "cannot be resumed" in capsys.readouterr().err


def test_a_busy_workspace_refuses_a_resume(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from engineering_team.runtime.locks import WorkspaceLock

    use_runner(monkeypatch, FakeRunner(fail={"foundation": 2}))
    start()
    lock = WorkspaceLock(project()).acquire("someone-else")
    try:
        assert resume(only_run().run_id) == 2
    finally:
        lock.release()

    assert "in use" in capsys.readouterr().err


# -- strategy selection --------------------------------------------------------------------------


def test_the_strategy_comes_from_the_flag_the_environment_or_defaults_to_hierarchical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Team:
        def __init__(self, ctx: object) -> None:
            self.ctx = ctx

        def crew(self) -> object:
            return type("Crew", (), {"kickoff": staticmethod(lambda inputs: None)})()

    monkeypatch.setattr(strategies, "EngineeringTeam", Team)
    base = ["--request", REQUEST, "--workspace-root", str(Path.cwd() / ROOT)]

    assert main.run([*base, "--project-name", "default"]) == 0
    assert RunStore(project("default")).latest().strategy == "hierarchical"  # type: ignore[union-attr]

    monkeypatch.setenv("ENGINEERING_STRATEGY", "single")
    use_runner(monkeypatch, FakeRunner())
    assert main.run([*base, "--project-name", "from-env"]) == 0
    assert RunStore(project("from-env")).latest().strategy == "single"  # type: ignore[union-attr]

    assert main.run([*base, "--project-name", "flag", "--strategy", "pipeline"]) == 0
    assert RunStore(project("flag")).latest().strategy == "pipeline"  # type: ignore[union-attr]


def test_an_unknown_strategy_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main.run(["--request", REQUEST, "--strategy", "swarm"])
    assert exit_info.value.code == 2
    with pytest.raises(ValueError, match="Unknown strategy 'swarm'"):
        strategies.get_strategy("swarm")


def test_the_single_agent_strategy_is_one_stage_with_all_the_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner())

    assert start("--strategy", "single") == 0

    assert runner.calls == [("build", None)]
    manifest = only_run()
    assert manifest.strategy == "single" and manifest.recipe == "single"
    assert [(r.name, r.status) for r in manifest.stages] == [("build", "succeeded")]
    assert runner.requests[0].teammate == "generalist_engineer"
    assert (project() / "docs" / "release-report.md").is_file()


def test_a_single_agent_run_resumes_like_any_other(monkeypatch: pytest.MonkeyPatch) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"build": 2}))
    assert start("--strategy", "single") == 1
    run_id = only_run().run_id
    runner = use_runner(monkeypatch, FakeRunner())

    assert resume(run_id) == 0

    assert runner.calls == [("build", None)] and "inspect" in runner.requests[0].note.lower()


def test_prepare_only_does_not_run_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = use_runner(monkeypatch, FakeRunner())

    assert start("--prepare-only") == 0

    assert runner.calls == []


# -- the pieces ------------------------------------------------------------------------------------


def test_the_retry_note_carries_the_previous_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = use_runner(monkeypatch, FakeRunner(fail={"spec": 1}))

    assert start() == 0

    spec_requests = [r for r in runner.requests if r.stage.name == "spec"]
    assert len(spec_requests) == 2
    assert spec_requests[0].note == ""
    assert "previous attempt failed: scripted failure in spec" in spec_requests[1].note
    types = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert types.count("stage.retry") == 1
    assert only_run().stages[0].attempts == 1  # one stage entry; the retry happened inside it


def test_missing_promised_files_fail_the_stage_even_when_the_agent_says_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Lazy(FakeRunner):
        def run(self, request: StageRequest):  # type: ignore[no-untyped-def]
            output = super().run(request)
            if request.stage.name == "foundation":
                (request.ctx.workspace.root / "README.md").unlink()
            return output

    use_runner(monkeypatch, Lazy())

    assert start() == 1

    manifest = only_run()
    assert stage_statuses(manifest)["foundation"] == "failed"
    assert "README.md (missing)" in manifest.stages[2].detail
    state = PipelineState.load(run_dir(manifest))
    assert state is not None and "README.md (missing)" in state.error


def test_an_invalid_plan_fails_the_plan_stage_with_every_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = Plan(
        work_packages=[
            PLAN.work_packages[0].model_copy(update={"depends_on": ["WP-9"]}),
            PLAN.work_packages[0],
        ]
    )
    use_runner(monkeypatch, FakeRunner(plan=broken))

    assert start() == 1

    detail = next(r for r in only_run().stages if r.name == "plan").detail
    assert "depends on unknown package 'WP-9'" in detail and "used twice" in detail


def test_the_event_log_tags_each_stage_and_ends_with_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())
    start()

    events = list(read_events(run_dir(only_run()) / "events.jsonl"))

    started = [e.stage for e in events if e.type == "stage.started"]
    assert started == list(STAGES)
    finished = [e for e in events if e.type == "pipeline.finished"]
    assert finished[-1].data["status"] == "succeeded"
    assert [e.type for e in events][-1] == "run.finished"


def test_the_manifest_summary_covers_the_whole_pipeline_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())
    start()

    summary = only_run().summary

    assert summary is not None and summary.status == "succeeded"
