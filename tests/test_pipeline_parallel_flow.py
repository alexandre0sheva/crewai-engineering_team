"""Parallel work packages through the whole pipeline: scopes, lanes, failures, resume."""

from __future__ import annotations

import json
import threading
import time

import pytest
from pipeline_fakes import SPEC, FakeRunner
from test_pipeline_crews import DOC, write
from test_pipeline_flow import (
    only_run,
    resume,
    run_dir,
    stage_statuses,
    start,
    use_runner,
)

from engineering_team.board.store import BoardStore
from engineering_team.contracts import Plan, WorkPackage
from engineering_team.pipeline import strategies
from engineering_team.pipeline.packages import SHARED_DENY
from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.testing import ScriptedLLM


def package(id_: str, *deps: str, **fields: object) -> WorkPackage:
    return WorkPackage(
        id=id_,
        title=f"Package {id_}",
        role="backend",
        depends_on=list(deps),
        owned_paths=[f"{id_.lower()}/**"],
        criteria_ids=["AC-1"],
        **fields,  # type: ignore[arg-type]
    )


THREE = Plan(stack="python", work_packages=[package("A"), package("B"), package("C")])


def implement_requests(runner: FakeRunner) -> list[StageRequest]:
    return [r for r in runner.requests if r.stage.name == "implement"]


def test_three_independent_packages_run_together_in_lanes_with_their_own_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(3, timeout=10)  # all three agents must be working at once

    def meet(request: StageRequest) -> None:
        if request.package is not None:
            barrier.wait()

    runner = use_runner(monkeypatch, FakeRunner(plan=THREE, on_call=meet))

    assert start() == 0

    requests = implement_requests(runner)
    assert sorted(r.lane for r in requests) == [1, 2, 3]
    for request in requests:
        assert request.package is not None
        assert request.write_scope is not None
        assert request.write_scope.allow == (f"{request.package.id.lower()}/**",)
        assert request.write_scope.deny == SHARED_DENY
    manifest = only_run()
    assert stage_statuses(manifest)["implement"] == "succeeded"
    board = BoardStore(run_dir(manifest))
    cards = board.cards(kind="work_package")
    assert [c.status for c in cards] == ["done"] * 3
    assert sorted(c.lane for c in cards) == [1, 2, 3]  # type: ignore[type-var]
    # Every package card was moved by the controller in its lane, and the board's WIP limit held.
    moves = [
        e for e in read_events(run_dir(manifest) / "events.jsonl") if e.type == "board.card_moved"
    ]
    assert {e.lane for e in moves if e.data["to_status"] == "in_progress" and e.agent} >= {1, 2, 3}


def test_with_one_lane_packages_run_in_order_and_keep_the_whole_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_MAX_PARALLEL", "1")
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def count(request: StageRequest) -> None:
        if request.package is None:
            return
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.02)
        with lock:
            active["now"] -= 1

    runner = use_runner(monkeypatch, FakeRunner(plan=THREE, on_call=count))

    assert start() == 0

    assert [r.package.id for r in implement_requests(runner) if r.package] == ["A", "B", "C"]
    assert active["peak"] == 1
    assert {r.lane for r in implement_requests(runner)} == {1}
    assert all(r.write_scope is None for r in implement_requests(runner))


def test_the_implement_stage_takes_one_packages_time_with_three_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def work(request: StageRequest) -> None:
        if request.package is not None:
            time.sleep(0.2)

    use_runner(monkeypatch, FakeRunner(plan=THREE, on_call=work))
    assert start() == 0
    record = next(r for r in only_run().stages if r.name == "implement")
    assert record.started and record.finished
    parallel = (record.finished - record.started).total_seconds()

    assert 0.2 <= parallel < 0.2 * 2  # about one package, not three


def test_overlapping_packages_are_serialised_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = Plan(
        work_packages=[
            WorkPackage(
                id="A", title="a", role="backend", owned_paths=["src/**"], criteria_ids=["AC-1"]
            ),
            WorkPackage(
                id="B", title="b", role="backend", owned_paths=["src/api/**"], criteria_ids=["AC-1"]
            ),
        ]
    )
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def count(request: StageRequest) -> None:
        if request.package is None:
            return
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1

    use_runner(monkeypatch, FakeRunner(plan=plan, on_call=count))

    assert start() == 0

    assert active["peak"] == 1
    events = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert "parallel.serialised" in events


# -- failures ---------------------------------------------------------------------------------


def test_a_required_package_failure_fails_the_stage_and_resume_redoes_only_what_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = Plan(work_packages=[package("A"), package("B"), package("C", "A"), package("D")])
    first = use_runner(monkeypatch, FakeRunner(plan=plan, fail={"implement:A": 2}))

    assert start() == 1

    manifest = only_run()
    assert stage_statuses(manifest)["implement"] == "failed"
    assert (
        "A (failed: scripted failure in implement:A)"
        in next(r for r in manifest.stages if r.name == "implement").detail
    )
    state = PipelineState.load(run_dir(manifest))
    assert state is not None
    assert {pid: p.status for pid, p in state.packages.items()} == {
        "A": "failed",
        "B": "succeeded",  # siblings were not cancelled
        "C": "skipped",  # depended on A
        "D": "succeeded",
    }
    assert "A" in state.packages["C"].error
    cards = {
        c.title.split(":")[0]: c.status
        for c in BoardStore(run_dir(manifest)).cards(kind="work_package")
    }
    assert cards == {"A": "failed", "B": "done", "C": "cancelled", "D": "done"}
    assert [r.package.id for r in implement_requests(first) if r.package].count(
        "A"
    ) == 2  # one retry

    second = use_runner(monkeypatch, FakeRunner(plan=plan))
    assert resume(manifest.run_id) == 0

    redone = sorted(r.package.id for r in implement_requests(second) if r.package)
    assert redone == ["A", "C"]  # B and D finished before the failure and are not run again
    assert only_run().status == "succeeded"
    final = PipelineState.load(run_dir(only_run()))
    assert final is not None and {p.status for p in final.packages.values()} == {"succeeded"}
    note = next(r.note for r in implement_requests(second) if r.package and r.package.id == "A")
    assert "inspect the workspace first" in note.lower() and "previous attempt failed" in note


def test_a_failed_optional_package_does_not_fail_the_run_and_integrate_sees_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = Plan(work_packages=[package("A"), package("B", required=False)])
    runner = use_runner(monkeypatch, FakeRunner(plan=plan, fail={"implement:B": 2}))

    assert start() == 0

    integrate = next(r for r in runner.requests if r.stage.name == "integrate")
    assert integrate.state.packages["B"].status == "failed"
    assert integrate.state.packages["A"].status == "succeeded"
    events = [e for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert any(
        e.type == "parallel.optional_missing" and e.data["packages"] == ["B"] for e in events
    )


def test_a_package_that_fails_once_is_retried_inside_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(plan=THREE, fail={"implement:B": 1}))

    assert start() == 0

    b_requests = [r for r in implement_requests(runner) if r.package and r.package.id == "B"]
    assert len(b_requests) == 2 and "previous attempt failed" in b_requests[1].note
    implement = next(r for r in only_run().stages if r.name == "implement")
    assert implement.attempts == 1  # the stage ran once; only the package was retried


def test_a_plan_that_gives_a_package_the_readme_is_sent_back_to_the_architect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = Plan(
        work_packages=[
            WorkPackage(
                id="A", title="a", role="backend", owned_paths=["**"], criteria_ids=["AC-1"]
            )
        ]
    )
    runner = use_runner(monkeypatch, FakeRunner(plan=bad))

    assert start() == 1

    plan_requests = [r for r in runner.requests if r.stage.name == "plan"]
    assert len(plan_requests) == 2  # one structured repair attempt
    assert "covers shared file(s)" in plan_requests[1].note
    assert "never shared root files" in plan_requests[1].note
    detail = next(r for r in only_run().stages if r.name == "plan").detail
    assert "covers shared file(s)" in detail and "shared files belong to foundation" in detail
    assert not any(r.stage.name == "implement" for r in runner.requests)


def test_a_package_naming_an_unknown_criterion_is_a_plan_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = Plan(
        work_packages=[
            package(
                "A",
            ).model_copy(update={"criteria_ids": ["AC-99"]})
        ]
    )
    use_runner(monkeypatch, FakeRunner(plan=bad))

    assert start() == 1

    detail = next(r for r in only_run().stages if r.name == "plan").detail
    assert "A names unknown criterion 'AC-99'" in detail
    assert SPEC.criteria[0].id == "AC-1"


# -- cancellation across lanes ----------------------------------------------------------------


def test_cancelling_stops_every_lane_and_the_run_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    both = threading.Barrier(2, timeout=10)
    stopped: list[str] = []

    def hold(request: StageRequest) -> None:
        if request.package is None or request.package.id == "C":
            return
        both.wait()
        if request.package.id == "A":
            request.ctx.cancel_event.set()
        assert request.ctx.cancel_event.wait(5)
        stopped.append(request.package.id)

    plan = Plan(work_packages=[package("A"), package("B"), package("C", "A")])
    use_runner(monkeypatch, FakeRunner(plan=plan, on_call=hold))

    assert start() == 130

    assert sorted(stopped) == ["A", "B"]  # both lanes ended, C (waiting on A) never started
    manifest = only_run()
    assert manifest.status == "cancelled"
    assert stage_statuses(manifest)["implement"] == "cancelled"
    resumed = use_runner(monkeypatch, FakeRunner(plan=plan))

    assert resume(manifest.run_id) == 0

    assert sorted(r.package.id for r in implement_requests(resumed) if r.package) == ["A", "B", "C"]


# -- real tools: write scope and lane tags ----------------------------------------------------


def scoped_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, ScriptedLLM]:
    plan = Plan(
        stack="python",
        work_packages=[
            WorkPackage(
                id="WP-1",
                title="Storage",
                role="backend",
                owned_paths=["src/storage/**"],
                criteria_ids=["AC-1"],
            )
        ],
    )
    llms = {
        "product_analyst": ScriptedLLM([SPEC.model_dump_json()]),
        "solution_architect": ScriptedLLM([write("docs/architecture.md"), plan.model_dump_json()]),
        "backend_engineer": ScriptedLLM(
            [
                write("README.md"),
                "Foundation is in place.",
                write("src/cli/main.py", "# not mine\n"),  # outside the package's scope
                write("src/storage/main.py", "VALUE = 1\n"),
                "WP-1 delivered AC-1.",
                write("docs/integration.md"),
                "Integrated.",
            ]
        ),
        "quality_engineer": ScriptedLLM(
            [write("docs/verification.md"), "verified", write("docs/release-report.md"), "done"]
        ),
    }
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )
    return llms


def test_a_scoped_agent_cannot_write_outside_its_package_but_the_foundation_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = scoped_run(monkeypatch)

    assert start() == 0

    from pathlib import Path

    root = Path.cwd() / "ws" / "demo"
    assert not (root / "src/cli/main.py").exists()  # refused: not in WP-1's scope
    assert (root / "src/storage/main.py").is_file()
    assert (root / "README.md").is_file() and (root / "docs/integration.md").is_file()
    refused = next(
        call for call in llms["backend_engineer"].calls if "outside your write scope" in call.prompt
    )
    assert "src/storage/**" in refused.prompt


def test_tool_events_of_a_package_carry_its_lane_and_other_stages_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_run(monkeypatch)
    start()

    events = list(read_events(run_dir(only_run()) / "events.jsonl"))

    implement_calls = [e for e in events if e.type == "tool.call" and e.stage == "implement"]
    assert implement_calls and {e.lane for e in implement_calls} == {1}
    spec_calls = [
        e for e in events if e.stage in ("spec", "plan", "foundation") and e.type == "tool.call"
    ]
    assert spec_calls and all(e.lane is None for e in spec_calls)
    llm_calls = [e for e in events if e.type == "llm.call" and e.stage == "implement"]
    assert llm_calls and {e.lane for e in llm_calls} == {1}  # CrewAI's own events too
    started = [e for e in events if e.type == "lane.started"]
    assert [(e.lane, e.data["unit"]) for e in started] == [(1, "WP-1")]


def test_the_model_rate_limiter_counts_every_model_call_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_MAX_RPM", "600")
    scoped_run(monkeypatch)
    seen: list[object] = []
    original = CrewStageRunner._agent

    def spy(self: CrewStageRunner, request: StageRequest):  # type: ignore[no-untyped-def]
        agent = original(self, request)
        seen.append(request.ctx.llm_rate)
        return agent

    monkeypatch.setattr(CrewStageRunner, "_agent", spy)

    assert start() == 0

    limiter = seen[0]
    assert limiter is not None and all(item is limiter for item in seen)
    usage = json.loads((run_dir(only_run()) / "usage.json").read_text(encoding="utf-8"))
    # Every model call of every stage agent took a slot (and none had to wait at 600 a minute).
    assert limiter.calls == usage["totals"]["calls"] == 14  # type: ignore[attr-defined]
    assert limiter.waits == 0  # type: ignore[attr-defined]
    assert DOC.startswith("# Doc")
