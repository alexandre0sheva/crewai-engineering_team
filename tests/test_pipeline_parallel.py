"""The parallel engine: lanes, ownership, failure isolation, cancellation, read-only fan-out."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from pipeline_fakes import SPEC

from engineering_team.contracts import Plan, Spec, WorkPackage
from engineering_team.pipeline.packages import (
    SHARED_DENY,
    PlanError,
    layers,
    paths_overlap,
    plan_problems,
    schedule,
)
from engineering_team.pipeline.parallel import (
    ReadOnlyJob,
    readonly_tools,
    run_parallel_readonly,
    run_work_packages,
)
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events, stage_scope
from engineering_team.runtime.requests import RateLimiter
from engineering_team.testing import ScriptedLLM, ToolCall, build_agent, build_task

MakeContext = Callable[..., RunContext]


def pkg(id_: str, *deps: str, paths: str | None = None, **fields: object) -> WorkPackage:
    return WorkPackage(
        id=id_,
        title=id_,
        role="backend",
        depends_on=list(deps),
        owned_paths=[paths or f"{id_.lower()}/**"],
        criteria_ids=["AC-1"],
        **fields,  # type: ignore[arg-type]
    )


def plan_of(*packages: WorkPackage) -> Plan:
    return Plan(work_packages=list(packages))


class Tracker:
    """Records when units start and end, and how many ran at once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.starts: dict[str, float] = {}
        self.ends: dict[str, float] = {}
        self.lanes: dict[str, int] = {}
        self.order: list[str] = []

    def job(self, work: Callable[[WorkPackage, int], str] | None = None):  # type: ignore[no-untyped-def]
        def run(package: WorkPackage, lane: int, note: str) -> str:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.starts[package.id] = time.monotonic()
                self.lanes[package.id] = lane
                self.order.append(package.id)
            try:
                return work(package, lane) if work else package.id
            finally:
                with self.lock:
                    self.active -= 1
                    self.ends[package.id] = time.monotonic()

        return run


def ids(outcomes) -> list[str]:  # type: ignore[no-untyped-def]
    return [o.id for o in outcomes]


# -- concurrency ------------------------------------------------------------------------------


def test_independent_packages_really_run_at_the_same_time(make_context: MakeContext) -> None:
    ctx = make_context()
    barrier = threading.Barrier(2, timeout=5)  # both must be inside their job to pass it
    tracker = Tracker()

    outcomes = run_work_packages(
        ctx,
        plan_of(pkg("A"), pkg("B")),
        tracker.job(lambda package, lane: (barrier.wait(), package.id)[1]),
        max_parallel=2,
    )

    assert [o.status for o in outcomes] == ["succeeded", "succeeded"]
    assert tracker.peak == 2 and sorted(tracker.lanes.values()) == [1, 2]


def test_dependencies_finish_before_their_dependents_start(make_context: MakeContext) -> None:
    ctx = make_context()
    tracker = Tracker()
    plan = plan_of(pkg("A"), pkg("B"), pkg("C", "A", "B"), pkg("D", "C"))

    outcomes = run_work_packages(
        ctx, plan, tracker.job(lambda p, lane: time.sleep(0.05) or p.id), max_parallel=3
    )

    assert ids(outcomes) == ["A", "B", "C", "D"] and all(o.ok for o in outcomes)
    assert tracker.starts["C"] >= max(tracker.ends["A"], tracker.ends["B"])
    assert tracker.starts["D"] >= tracker.ends["C"]


def test_three_packages_take_about_one_packages_time_in_three_lanes(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    plan = plan_of(pkg("A"), pkg("B"), pkg("C"))

    def timed(max_parallel: int) -> float:
        tracker = Tracker()
        started = time.monotonic()
        run_work_packages(
            ctx, plan, tracker.job(lambda p, lane: time.sleep(0.2) or p.id), max_parallel
        )
        return time.monotonic() - started

    assert timed(3) < 0.2 * 1.9  # roughly one package's time, not three
    assert timed(1) >= 0.2 * 3 * 0.95


def test_max_parallel_one_is_strictly_sequential_in_dependency_order(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    tracker = Tracker()
    plan = plan_of(pkg("C", "A"), pkg("A"), pkg("B"))

    run_work_packages(ctx, plan, tracker.job(lambda p, lane: time.sleep(0.02) or p.id), 1)

    assert tracker.peak == 1 and set(tracker.lanes.values()) == {1}
    assert tracker.order == ["A", "B", "C"]  # layer by layer, plan order within a layer


def test_a_lane_is_the_lowest_free_number_and_never_exceeds_the_limit(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    tracker = Tracker()
    plan = plan_of(*(pkg(f"P{n}") for n in range(6)))

    run_work_packages(ctx, plan, tracker.job(lambda p, lane: time.sleep(0.03) or p.id), 2)

    assert set(tracker.lanes.values()) <= {1, 2} and tracker.peak == 2


def test_outcomes_come_back_in_plan_order_whatever_finished_first(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    delays = {"A": 0.15, "B": 0.05, "C": 0.0}
    tracker = Tracker()

    outcomes = run_work_packages(
        ctx,
        plan_of(pkg("A"), pkg("B"), pkg("C")),
        tracker.job(lambda p, lane: time.sleep(delays[p.id]) or p.id),
        max_parallel=3,
    )

    assert ids(outcomes) == ["A", "B", "C"]
    assert tracker.ends["C"] < tracker.ends["B"] < tracker.ends["A"]  # finished in reverse


def test_packages_that_may_overlap_run_one_after_the_other_with_a_warning(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    tracker = Tracker()
    plan = plan_of(
        pkg("A", paths="src/**"), pkg("B", paths="src/api/**"), pkg("C", paths="docs/**")
    )

    outcomes = run_work_packages(
        ctx, plan, tracker.job(lambda p, lane: time.sleep(0.05) or p.id), max_parallel=3
    )

    assert ids(outcomes) == ["A", "B", "C"] and all(o.ok for o in outcomes)
    assert tracker.starts["B"] >= tracker.ends["A"]  # overlap: serialised
    assert tracker.starts["C"] < tracker.ends["A"]  # disjoint from A: ran beside it
    warnings = [
        e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "parallel.serialised"
    ]
    assert warnings and warnings[0].data["packages"] == [["A", "C"], ["B"]]


# -- failure isolation ------------------------------------------------------------------------


def test_a_failed_package_is_retried_then_isolated_and_its_dependents_are_skipped(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    notes: list[str] = []

    def job(package: WorkPackage, lane: int, note: str) -> str:
        if package.id == "A":
            notes.append(note)
            raise RuntimeError("compile error")
        return f"{package.id} ok"

    plan = plan_of(pkg("A"), pkg("B"), pkg("C", "A"), pkg("D", "C"), pkg("E", "B"))

    outcomes = run_work_packages(ctx, plan, job, max_parallel=3, retry=1)

    by_id = {o.id: o for o in outcomes}
    assert ids(outcomes) == ["A", "B", "C", "D", "E"]
    assert by_id["A"].status == "failed" and by_id["A"].attempts == 2
    assert "compile error" in by_id["A"].error
    assert notes == ["", "The previous attempt failed: compile error"]
    assert by_id["B"].ok and by_id["E"].ok  # independent work carried on
    assert by_id["C"].status == "skipped" and "A" in by_id["C"].error
    assert by_id["D"].status == "skipped"  # skipped transitively


def test_a_package_that_fails_once_succeeds_on_the_retry(make_context: MakeContext) -> None:
    ctx = make_context()
    attempts: list[str] = []

    def job(package: WorkPackage, lane: int, note: str) -> str:
        attempts.append(note)
        if not note:
            raise ValueError("flaky")
        return "fixed"

    (outcome,) = run_work_packages(ctx, plan_of(pkg("A")), job, max_parallel=2, retry=1)

    assert outcome.status == "succeeded" and outcome.attempts == 2 and outcome.summary == "fixed"
    types = [e.type for e in read_events(ctx.run_dir / "events.jsonl")]
    assert "lane.retry" in types


def test_packages_that_already_succeeded_are_not_run_again(make_context: MakeContext) -> None:
    ctx = make_context()
    tracker = Tracker()

    outcomes = run_work_packages(
        ctx,
        plan_of(pkg("A"), pkg("B", "A")),
        tracker.job(),
        max_parallel=2,
        done={"A"},
    )

    assert [(o.id, o.status) for o in outcomes] == [("A", "reused"), ("B", "succeeded")]
    assert tracker.order == ["B"]


# -- cancellation and events ------------------------------------------------------------------


def test_cancelling_stops_every_lane_and_starts_nothing_more(make_context: MakeContext) -> None:
    ctx = make_context()
    started = threading.Barrier(3, timeout=5)
    tracker = Tracker()

    def job(package: WorkPackage, lane: int) -> str:
        started.wait()
        assert ctx.cancel_event.wait(5), "a lane was not told to stop"
        return package.id

    plan = plan_of(pkg("A"), pkg("B"), pkg("C", "A"))
    canceller = threading.Thread(target=lambda: (started.wait(), ctx.cancel_event.set()))
    canceller.start()

    with pytest.raises(RunCancelled):
        run_work_packages(ctx, plan, tracker.job(job), max_parallel=2)
    canceller.join(5)

    assert sorted(tracker.order) == ["A", "B"]  # C, in the next layer, never started
    assert tracker.active == 0  # both lanes have ended


def test_events_carry_the_lane_and_stage_of_the_unit_that_emitted_them(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    barrier = threading.Barrier(3, timeout=5)

    def job(package: WorkPackage, lane: int) -> str:
        barrier.wait()  # all three are inside at once: tags must not leak between them
        for step in range(3):
            ctx.events.emit("probe", unit=package.id, step=step)
        return package.id

    tracker = Tracker()
    with stage_scope("implement"):
        run_work_packages(ctx, plan_of(pkg("A"), pkg("B"), pkg("C")), tracker.job(job), 3)

    events = list(read_events(ctx.run_dir / "events.jsonl"))
    probes = [e for e in events if e.type == "probe"]
    assert len(probes) == 9
    for event in probes:
        assert event.lane == tracker.lanes[event.data["unit"]]
        assert event.stage == "implement"
    started = {e.data["unit"]: e.lane for e in events if e.type == "lane.started"}
    assert started == tracker.lanes
    assert len(set(started.values())) == 3


# -- plan validation --------------------------------------------------------------------------


def test_layers_group_packages_by_dependency_depth() -> None:
    plan = plan_of(pkg("A"), pkg("B", "A"), pkg("C"), pkg("D", "B", "C"))

    assert [[p.id for p in layer] for layer in layers(plan.work_packages)] == [
        ["A", "C"],
        ["B"],
        ["D"],
    ]
    cyclic = plan_of(pkg("A", "B"), pkg("B", "A"))
    with pytest.raises(PlanError, match="cycle"):
        layers(cyclic.work_packages)


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        (["src/api/**"], ["src/cli/**"], False),
        (["src/**"], ["src/api/**"], True),
        (["src/api/**"], ["src/api/models/**"], True),
        (["src/api"], ["src/apiary/**"], False),
        (["frontend/**", "e2e/**"], ["backend/**"], False),
        (["frontend/**", "shared/**"], ["shared/**"], True),
        (["a.py"], ["b.py"], False),
        (["a.py"], ["a.py"], True),
        (["tests"], ["tests/"], True),
        (["*.md"], ["docs/**"], True),  # may match anywhere: assume overlap
        (["src/*.py"], ["src/api/**"], True),
        (["/docs/"], ["docs/x.md"], True),
    ],
)
def test_ownership_overlap_is_conservative(
    first: list[str], second: list[str], overlap: bool
) -> None:
    assert paths_overlap(first, second) is overlap
    assert paths_overlap(second, first) is overlap


def test_schedule_splits_a_layer_into_batches_of_disjoint_packages() -> None:
    layer = [pkg("A", paths="src/**"), pkg("B", paths="docs/**"), pkg("C", paths="src/api/**")]

    assert [[p.id for p in batch] for batch in schedule(layer)] == [["A", "B"], ["C"]]
    assert [[p.id for p in batch] for batch in schedule(layer[1:2])] == [["B"]]


def test_a_valid_plan_has_no_problems() -> None:
    plan = plan_of(pkg("A"), pkg("B", "A", paths="b/**"))

    assert plan_problems(plan, SPEC) == []
    assert plan_problems(plan) == []


def test_plan_problems_name_every_ownership_and_criteria_mistake() -> None:
    spec = Spec(title="t", criteria=SPEC.criteria)
    plan = plan_of(
        pkg("A", paths="**"),  # owns everything, including shared root files
        pkg("B", paths="README.md"),
        WorkPackage(id="C", title="c", role="backend", criteria_ids=["AC-1"]),  # no paths
        WorkPackage(id="D", title="d", role="backend", owned_paths=["d/**"]),  # no criteria
        WorkPackage(id="E", title="e", role="backend", owned_paths=["e/**"], criteria_ids=["AC-9"]),
    )

    problems = "\n".join(plan_problems(plan, spec))

    assert "A owns '**', which covers shared file(s) README.md" in problems
    assert "B owns 'README.md'" in problems
    assert "C owns no paths" in problems
    assert "D delivers no acceptance criterion" in problems
    assert "E names unknown criterion 'AC-9'" in problems


def test_a_frontend_directory_may_own_its_own_manifest() -> None:
    plan = plan_of(pkg("A", paths="frontend/**"))  # frontend/package.json is not a root file

    assert plan_problems(plan, SPEC) == []


def test_shared_root_files_are_denied_to_scoped_agents() -> None:
    from engineering_team.tools import WriteScope

    scope = WriteScope(allow=("**",), deny=SHARED_DENY)

    assert not scope.permits("package.json") and not scope.permits("README.md")
    assert scope.permits("frontend/package.json") and scope.permits("src/app.py")


# -- the shared rate limit --------------------------------------------------------------------


def test_the_rate_limiter_lets_max_rpm_through_then_waits_for_the_window() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    limiter = RateLimiter(2, threading.Event(), clock=clock, window=60.0)

    def advance(seconds: float) -> bool:  # stands in for waiting on the cancel event
        sleeps.append(seconds)
        now[0] += seconds
        return False

    limiter._cancel.wait = advance  # type: ignore[union-attr,method-assign]
    assert limiter.check_or_wait() and limiter.check_or_wait()
    assert limiter.waits == 0
    now[0] = 10.0

    assert limiter.check_or_wait()  # the third call has to wait out the first one's minute

    assert limiter.calls == 3 and limiter.waits == 1
    assert now[0] >= 60.0 and sum(sleeps) == pytest.approx(50.0, abs=1.0)


def test_waiting_for_the_rate_limit_ends_when_the_run_is_cancelled() -> None:
    cancel = threading.Event()
    limiter = RateLimiter(1, cancel)
    limiter.check_or_wait()
    threading.Timer(0.1, cancel.set).start()

    started = time.monotonic()
    assert limiter.check_or_wait() is True

    assert time.monotonic() - started < 3


def test_agents_of_every_lane_share_one_limiter(make_context: MakeContext) -> None:
    from engineering_team.pipeline.recipes import load_recipe
    from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.settings import load_settings

    ctx = make_context(
        settings=load_settings().with_overrides({"parallel.max_rpm": 600}, source="t")
    )
    assert ctx.llm_rate is not None and ctx.llm_rate.max_rpm == 600
    runner = CrewStageRunner(llm_factory=lambda key: ScriptedLLM(["x"]))
    stage = load_recipe("new").stage("foundation")

    first = runner._agent(
        StageRequest(
            ctx=ctx, stage=stage, teammate="backend_engineer", state=PipelineState(), lane=1
        )
    )
    second = runner._agent(
        StageRequest(
            ctx=ctx, stage=stage, teammate="backend_engineer", state=PipelineState(), lane=2
        )
    )

    assert first._rpm_controller is ctx.llm_rate and second._rpm_controller is ctx.llm_rate


def test_no_rate_limiter_exists_unless_max_rpm_is_set(make_context: MakeContext) -> None:
    assert make_context().llm_rate is None


# -- read-only fan-out ------------------------------------------------------------------------


def reviewer_script(report: str, stray: str) -> ScriptedLLM:
    return ScriptedLLM(
        [
            ToolCall("Write Project File", {"path": stray, "content": "sneaky"}),
            ToolCall("Write Project File", {"path": report, "content": "# Findings\nnone\n"}),
            "reviewed",
        ]
    )


def test_read_only_jobs_run_together_and_write_only_to_their_own_report(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("src/app.py", "x = 1\n")
    barrier = threading.Barrier(2, timeout=5)
    llms = {
        "security": reviewer_script("reports/security.md", "src/app.py"),
        "style": reviewer_script("reports/style.md", "reports/security.md"),
    }
    seen_tools: dict[str, set[str]] = {}

    def runner(job: ReadOnlyJob, tools: list, lane: int) -> str:  # type: ignore[type-arg]
        seen_tools[job.name] = {tool.name for tool in tools}
        barrier.wait()  # proves the two agents ran concurrently
        agent = build_agent(ctx, llms[job.name], tools=tools)
        return agent.execute_task(build_task(agent))

    jobs = [
        ReadOnlyJob("security", "code_reviewer", "reports/security.md"),
        ReadOnlyJob("style", "code_reviewer", "reports/style.md"),
    ]

    results = run_parallel_readonly(ctx, jobs, runner, max_parallel=2)

    assert [(r.name, r.status, r.summary) for r in results] == [
        ("security", "succeeded", "reviewed"),
        ("style", "succeeded", "reviewed"),
    ]
    assert sorted(r.lane for r in results if r.lane) == [1, 2]
    assert (
        (ctx.workspace.root / "reports/security.md")
        .read_text(encoding="utf-8")
        .startswith("# Findings")
    )
    assert (ctx.workspace.root / "reports/style.md").is_file()
    # Writing anywhere else was refused, naming the one path each job owns.
    assert (ctx.workspace.root / "src/app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "outside your write scope" in llms["security"].calls[1].prompt
    assert "reports/security.md" in llms["security"].calls[1].prompt
    assert "outside your write scope" in llms["style"].calls[1].prompt
    # Reading and navigating is allowed; running commands or deleting is not even offered.
    for tools in seen_tools.values():
        assert {"Read Project File", "Search Project Files"} <= tools
        assert "Run Project Command" not in tools and "Run Tests" not in tools


def test_a_failing_read_only_job_does_not_stop_the_others(make_context: MakeContext) -> None:
    ctx = make_context()

    def runner(job: ReadOnlyJob, tools: list, lane: int) -> str:  # type: ignore[type-arg]
        if job.name == "bad":
            raise RuntimeError("model unavailable")
        return f"{job.name} done"

    jobs = [
        ReadOnlyJob("good", "code_reviewer", "r/good.md"),
        ReadOnlyJob("bad", "code_reviewer", "r/bad.md"),
        ReadOnlyJob("fine", "code_reviewer", "r/fine.md"),
    ]

    results = run_parallel_readonly(ctx, jobs, runner)

    assert [(r.name, r.status) for r in results] == [
        ("good", "succeeded"),
        ("bad", "failed"),
        ("fine", "succeeded"),
    ]
    assert "model unavailable" in results[1].error and results[0].summary == "good done"


def test_read_only_jobs_need_distinct_names_and_report_paths(make_context: MakeContext) -> None:
    ctx = make_context()
    same_path = [ReadOnlyJob("a", "t", "r.md"), ReadOnlyJob("b", "t", "r.md")]
    same_name = [ReadOnlyJob("a", "t", "x.md"), ReadOnlyJob("a", "t", "y.md")]

    for jobs in (same_path, same_name):
        with pytest.raises(ValueError, match="distinct"):
            run_parallel_readonly(ctx, jobs, lambda job, tools, lane: "")


def test_read_only_tools_cannot_write_outside_the_report(make_context: MakeContext) -> None:
    ctx = make_context()
    tools = {t.name: t for t in readonly_tools(ctx, ReadOnlyJob("x", "t", "out/report.md"), 1)}

    assert (
        tools["Write Project File"].run(path="out/report.md", content="ok").startswith("Wrote")
        or (ctx.workspace.root / "out/report.md").is_file()
    )
    refusal = tools["Write Project File"].run(path="elsewhere.md", content="no")
    assert refusal.startswith("ERROR:") and "out/report.md" in refusal
    assert tools["Delete Project Path"].run(path="README.md").startswith("ERROR:")
