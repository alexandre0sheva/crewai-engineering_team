from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from crewai import Crew, Process

from engineering_team import main
from engineering_team.contracts import RunManifest, UsageReport
from engineering_team.pricing import build_table
from engineering_team.runtime.bridge import bind_run, flush_bridge
from engineering_team.runtime.budget import (
    WARN_FRACTION,
    Budget,
    BudgetExceeded,
    BudgetGuard,
)
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import RunRecorder, format_summary
from engineering_team.runtime.usage import UsageTracker
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, Turn, build_agent, build_task
from engineering_team.tools import ProjectWorkspace, build_tools

MakeContext = Callable[..., RunContext]
REQUEST = "Build a tiny notes app."


def _context(tmp_path: Path, **overrides: object) -> RunContext:
    settings = load_settings(overrides=overrides)  # type: ignore[arg-type]
    return RunContext.create(settings, ProjectWorkspace.create(tmp_path / "project"))


def _run_crew(ctx: RunContext, llm: ScriptedLLM) -> None:
    agent = build_agent(ctx, llm, role="Backend Engineer")
    Crew(
        agents=[agent],
        tasks=[build_task(agent)],
        process=Process.sequential,
        verbose=False,
        tracing=False,
        memory=False,
        cache=False,
    ).kickoff()


# -- usage accounting -----------------------------------------------------------------------


def test_usage_is_summed_per_stage_agent_and_model() -> None:
    tracker = UsageTracker()
    tracker.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        agent="Backend",
        stage="build",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "cached_prompt_tokens": 40,
            "reasoning_tokens": 4,
        },
    )
    tracker.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        agent="Backend",
        stage="build",
        usage={"prompt_tokens": 200, "completion_tokens": 20},
    )
    tracker.emit(
        "llm.call",
        model="openai/gpt-6.1-sol",
        agent="Lead",
        stage="plan",
        usage={"prompt_tokens": 1000, "completion_tokens": 100, "cache_creation_tokens": 50},
    )
    tracker.emit("tool.call", tool="Read Project File")
    tracker.emit("task.started")  # ignored

    report = tracker.report(build_table())

    assert report.totals.model_dump(exclude={"schema_version"}) == {
        "prompt_tokens": 1300,
        "completion_tokens": 130,
        "cached_prompt_tokens": 40,
        "reasoning_tokens": 4,
        "cache_creation_tokens": 50,
        "total_tokens": 1430,
        "calls": 3,
    }
    assert report.tool_calls == 1
    assert report.by_model["openai/gpt-6-luna"].total_tokens == 330
    assert report.by_agent["Lead"].prompt_tokens == 1000
    assert report.by_stage["build"].calls == 2 and report.by_stage["plan"].calls == 1
    assert (
        len(report.rows) == 2 + 0 or len(report.rows) == 2
    )  # (build,Backend,luna) and (plan,Lead,sol)


def test_cost_is_exact_for_known_models_and_unknown_when_any_model_is_unpriced() -> None:
    tracker = UsageTracker()
    tracker.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    )

    known = tracker.report(build_table())

    assert known.estimated_cost_usd == pytest.approx(0.10 + 0.50)
    assert known.unpriced_models == []

    tracker.emit(
        "llm.call", model="scripted/fake", usage={"prompt_tokens": 5, "completion_tokens": 5}
    )
    mixed = tracker.report(build_table())

    assert mixed.estimated_cost_usd is None  # never a partial sum shown as the total
    assert mixed.known_cost_usd == pytest.approx(0.60)
    assert mixed.unpriced_models == ["scripted/fake"]
    assert mixed.cost_by_model["scripted/fake"] is None
    assert mixed.totals.total_tokens == 2_000_010


def test_a_run_with_no_model_calls_costs_zero_not_unknown() -> None:
    report = UsageTracker().report(build_table())

    assert report.totals.calls == 0 and report.estimated_cost_usd == 0.0


def test_usage_can_be_rebuilt_from_the_event_log(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    ctx.events.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        agent="A",
        stage="s",
        usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    ctx.events.emit("tool.call", tool="t")

    rebuilt = UsageTracker.from_events(read_events(ctx.run_dir / "events.jsonl"))

    assert rebuilt.report(ctx.prices) == ctx.usage.report(ctx.prices)
    assert rebuilt.tool_calls == 1


def test_a_scripted_crew_reports_exact_tokens_through_the_bridge(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    llm = ScriptedLLM(
        [
            Turn(ToolCall("List Project Files", {}), prompt_tokens=100, completion_tokens=10),
            Turn("done", prompt_tokens=150, completion_tokens=20),
        ]
    )

    with bind_run(ctx.run_id, ctx.events):
        _run_crew(ctx, llm)
    flush_bridge()

    report = ctx.usage.report(ctx.prices)
    assert (report.totals.prompt_tokens, report.totals.completion_tokens) == (250, 30)
    assert report.totals.calls == 2 and report.tool_calls == 1
    assert report.by_agent == {"Backend Engineer": report.totals}
    assert report.unpriced_models == ["scripted/fake"] and report.estimated_cost_usd is None


def test_a_price_override_gives_the_scripted_model_a_cost(tmp_path: Path) -> None:
    config = tmp_path / "engineering-team.toml"
    config.write_text('[pricing."scripted/fake"]\ninput = 10\noutput = 20\n', encoding="utf-8")
    settings = load_settings(config_file=str(config))
    ctx = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "project"))
    llm = ScriptedLLM([Turn("done", prompt_tokens=1_000_000, completion_tokens=500_000)])

    with bind_run(ctx.run_id, ctx.events):
        _run_crew(ctx, llm)
    flush_bridge()

    assert ctx.usage.report(ctx.prices).estimated_cost_usd == pytest.approx(10 + 10)


def test_events_inside_a_stage_are_tagged_and_usage_is_grouped_by_it(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    recorder = RunRecorder.begin(ctx, request=REQUEST)
    llm = ScriptedLLM([Turn("done", 100, 10)])

    with recorder.running():
        with recorder.stage("build"):
            _run_crew(ctx, llm)
        with recorder.stage("verify"):
            pass

    report = ctx.usage.report(ctx.prices)
    assert set(report.by_stage) == {"build"} and report.by_stage["build"].total_tokens == 110
    events = list(read_events(ctx.run_dir / "events.jsonl"))
    assert {e.stage for e in events if e.type == "llm.call"} == {"build"}
    assert [e.type for e in events if e.type.startswith("stage.")] == [
        "stage.started",
        "stage.finished",
        "stage.started",
        "stage.finished",
    ]
    stages = recorder.manifest.stages
    assert [(s.name, s.status, s.attempts) for s in stages] == [
        ("build", "succeeded", 1),
        ("verify", "succeeded", 1),
    ]


def test_a_finished_run_writes_usage_json_and_a_summary(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    recorder = RunRecorder.begin(ctx, request=REQUEST)
    llm = ScriptedLLM([Turn(ToolCall("List Project Files", {}), 100, 10), Turn("done", 150, 20)])

    with recorder.running():
        _run_crew(ctx, llm)

    usage = UsageReport.model_validate_json((ctx.run_dir / "usage.json").read_text("utf-8"))
    assert usage.totals.total_tokens == 280 and usage.rows[0].agent == "Backend Engineer"
    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    summary = manifest.summary
    assert summary is not None and summary.status == "succeeded"
    assert (summary.usage.prompt_tokens, summary.usage.completion_tokens) == (250, 30)
    assert summary.tool_calls == 1 and summary.estimated_cost_usd is None
    assert summary.unpriced_models == ["scripted/fake"]
    assert summary.budget_status.state == "unlimited"
    finished = list(read_events(ctx.run_dir / "events.jsonl"))[-1]
    assert finished.data["summary"]["usage"]["total_tokens"] == 280


def test_the_summary_text_shows_exact_counts_and_a_cost_or_unknown(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    recorder = RunRecorder.begin(ctx, request=REQUEST)
    ctx.events.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        usage={"prompt_tokens": 4200, "completion_tokens": 1110, "cached_prompt_tokens": 300},
    )
    priced = format_summary(recorder.summary("succeeded"))
    ctx.events.emit(
        "llm.call", model="mystery/model", usage={"prompt_tokens": 1, "completion_tokens": 1}
    )
    unknown = format_summary(recorder.summary("succeeded"))

    assert priced.splitlines()[0] == (
        "Usage: 5,310 tokens (prompt 4,200, of which 300 cached; completion 1,110) "
        "in 1 model call(s) and 0 tool call(s)"
    )
    assert priced.splitlines()[1].startswith("Estimated cost: $0.0")
    assert "Estimated cost: unknown (no price for mystery/model" in unknown
    assert "$0.00" not in unknown.splitlines()[1]


# -- budgets --------------------------------------------------------------------------------


def _guard(
    budget: Budget, usage: UsageTracker | None = None, clock: Callable[[], float] | None = None
) -> tuple[BudgetGuard, UsageTracker, threading.Event, list[tuple[str, dict]]]:  # type: ignore[type-arg]
    usage = usage or UsageTracker()
    cancel = threading.Event()
    seen: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    class Sink:
        def emit(self, type: str, **data: object) -> None:
            seen.append((type, data))

    guard = BudgetGuard(
        budget, usage, build_table(), cancel, events=Sink(), clock=clock or (lambda: 0.0)
    )
    return guard, usage, cancel, seen


def _spend(usage: UsageTracker, guard: BudgetGuard, tokens: int) -> None:
    event = {
        "model": "openai/gpt-6-luna",
        "usage": {"prompt_tokens": tokens, "completion_tokens": 0},
    }
    usage.emit("llm.call", **event)
    guard.emit("llm.call", **event)


def test_no_limits_means_unlimited_and_nothing_trips() -> None:
    guard, usage, cancel, seen = _guard(Budget())

    _spend(usage, guard, 10_000_000)

    assert guard.check().state == "unlimited"
    assert not cancel.is_set() and seen == []


def test_a_warning_is_emitted_once_at_eighty_percent() -> None:
    guard, usage, cancel, seen = _guard(Budget(max_tokens=1000))

    _spend(usage, guard, 700)
    assert guard.status().state == "ok" and seen == []
    _spend(usage, guard, 100)  # exactly 80%
    _spend(usage, guard, 50)

    warnings = [data for type_, data in seen if type_ == "budget.warning"]
    assert len(warnings) == 1
    assert warnings[0]["limit"] == "max_tokens" and warnings[0]["fraction"] == pytest.approx(0.8)
    assert WARN_FRACTION == 0.8
    status = guard.check()
    assert status.state == "warning" and not cancel.is_set()
    assert status.limits[0].used == 850 and status.limits[0].max == 1000


def test_passing_a_limit_cancels_the_run_and_check_raises_at_the_safe_point() -> None:
    guard, usage, cancel, seen = _guard(Budget(max_tokens=1000))

    _spend(usage, guard, 1001)

    assert cancel.is_set() and guard.tripped is not None
    assert [type_ for type_, _ in seen] == [
        "budget.warning",
        "budget.exceeded",
    ] or "budget.exceeded" in [t for t, _ in seen]
    with pytest.raises(BudgetExceeded, match="max_tokens reached 1001") as caught:
        guard.check()
    assert caught.value.limit == "max_tokens" and "In-flight" in str(caught.value)
    assert guard.status().state == "exceeded" and guard.status().exceeded is not None


def test_exactly_reaching_a_limit_is_not_passing_it() -> None:
    guard, usage, cancel, _ = _guard(Budget(max_tokens=1000))

    _spend(usage, guard, 1000)

    assert guard.check().state == "warning" and not cancel.is_set()


def test_only_the_first_overspend_is_reported() -> None:
    guard, usage, _, seen = _guard(Budget(max_tokens=100))

    _spend(usage, guard, 500)
    _spend(usage, guard, 500)

    assert [t for t, _ in seen].count("budget.exceeded") == 1


def test_the_wall_clock_limit_uses_the_guards_clock() -> None:
    now = [0.0]
    guard, _, cancel, _ = _guard(Budget(max_wall_seconds=60), clock=lambda: now[0])

    now[0] = 59.0
    assert guard.check().state == "warning"
    now[0] = 61.0
    with pytest.raises(BudgetExceeded, match="max_wall_seconds"):
        guard.check()
    assert cancel.is_set()


def test_a_cost_limit_is_enforced_with_known_prices() -> None:
    guard, usage, cancel, _ = _guard(Budget(max_cost_usd=0.5))

    usage.emit(
        "llm.call",
        model="openai/gpt-6-luna",
        usage={"prompt_tokens": 0, "completion_tokens": 1_200_000},
    )  # $0.60
    guard.emit("llm.call")

    assert cancel.is_set()
    with pytest.raises(BudgetExceeded, match=r"max_cost_usd reached \$0.6000"):
        guard.check()


def test_a_cost_limit_cannot_be_enforced_for_an_unpriced_model_and_says_so() -> None:
    guard, usage, cancel, seen = _guard(Budget(max_cost_usd=0.01))

    usage.emit(
        "llm.call",
        model="mystery/model",
        usage={"prompt_tokens": 10_000_000, "completion_tokens": 10_000_000},
    )
    guard.emit("llm.call")

    assert not cancel.is_set()
    notes = guard.check().notes
    assert notes and "mystery/model" in notes[0] and "cannot be fully enforced" in notes[0]
    assert [d.get("limit") for t, d in seen if t == "budget.warning"] == ["max_cost_usd"]


def test_the_tool_call_limit_refuses_the_call_that_would_pass_it() -> None:
    guard, usage, cancel, seen = _guard(Budget(max_tool_calls=2))

    assert guard.tool_gate("a") is None
    usage.emit("tool.call", tool="a")
    assert guard.tool_gate("b") is None
    usage.emit("tool.call", tool="b")
    refusal = guard.tool_gate("c")

    assert refusal is not None and "max_tool_calls" in refusal
    assert cancel.is_set() and [t for t, _ in seen if t == "budget.exceeded"] == ["budget.exceeded"]
    assert guard.tool_gate("d") == refusal  # everything after is refused too
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_repair_rounds_are_capped() -> None:
    guard, _, _, _ = _guard(Budget(max_repair_rounds=2))

    assert [guard.may_repair(used) for used in range(4)] == [True, True, False, False]


def test_budgets_come_from_settings() -> None:
    settings = load_settings(
        overrides={
            "budget.max_cost_usd": 2.5,
            "budget.max_tokens": 9000,
            "budget.max_repair_rounds": 1,
        }
    )

    budget = Budget.from_settings(settings.budget)

    assert (budget.max_cost_usd, budget.max_tokens, budget.max_tool_calls) == (2.5, 9000, None)
    assert budget.max_repair_rounds == 1


# -- budgets in a real run ------------------------------------------------------------------


def test_the_tool_gate_stops_a_run_through_the_real_tools(tmp_path: Path) -> None:
    ctx = _context(tmp_path, **{"budget.max_tool_calls": 2})
    tools = {tool.name: tool for tool in build_tools(ctx)}

    first = tools["Project Tree"].run()
    second = tools["Project Tree"].run()
    third = tools["Write Project File"].run(path="late.txt", content="x")
    fourth = tools["Project Tree"].run()

    assert not first.startswith("ERROR") and not second.startswith("ERROR")
    assert third.startswith("ERROR: Budget exceeded: max_tool_calls")
    assert fourth.startswith("ERROR: Budget exceeded") and "Stop working" in fourth
    assert not (ctx.workspace.root / "late.txt").exists() and ctx.cancel_event.is_set()


def test_an_overspending_agent_is_wound_down_and_the_run_fails_with_the_budget_error(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path, **{"budget.max_tokens": 500})
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    def after_delivery(call: ToolCall) -> Callable[..., ToolCall]:
        def reply(messages: list, tools: list | None) -> ToolCall:  # type: ignore[type-arg]
            flush_bridge()  # make usage events land before the next model turn
            return call

        return reply

    llm = ScriptedLLM(
        [
            Turn(ToolCall("Write Project File", {"path": "one.txt", "content": "1"}), 300, 0),
            Turn(
                after_delivery(ToolCall("Write Project File", {"path": "two.txt", "content": "2"})),
                300,
                0,
            ),
            Turn(
                after_delivery(
                    ToolCall("Write Project File", {"path": "three.txt", "content": "3"})
                ),
                100,
                0,
            ),
            Turn("stopping", 50, 0),
        ]
    )

    with pytest.raises(BudgetExceeded, match="max_tokens"), recorder.running():
        _run_crew(ctx, llm)

    root = ctx.workspace.root
    assert (root / "one.txt").is_file()
    # Call 2's usage (600 > 500) is delivered before call 3 returns, so the third write is
    # refused. Whether the second write ran is a race with that delivery, so it is not asserted.
    assert not (root / "three.txt").exists()
    manifest = recorder.manifest
    assert manifest.status == "failed"  # a budget stop is a failure, not a cancellation
    assert manifest.summary is not None and manifest.summary.budget_status.state == "exceeded"
    events = list(read_events(ctx.run_dir / "events.jsonl"))
    assert "budget.exceeded" in [e.type for e in events]
    assert events[-1].data["error"].startswith("BudgetExceeded: Budget exceeded: max_tokens")


def test_a_stage_boundary_stops_an_overspent_run(tmp_path: Path) -> None:
    ctx = _context(tmp_path, **{"budget.max_tokens": 1000})
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    with pytest.raises(BudgetExceeded), recorder.running():
        with recorder.stage("plan"):
            pass
        with pytest.raises(BudgetExceeded), recorder.stage("build"):
            ctx.events.emit(
                "llm.call",
                model="openai/gpt-6-luna",
                usage={"prompt_tokens": 2000, "completion_tokens": 0},
            )
        with recorder.stage("verify"):  # entering a stage re-checks: never starts
            pytest.fail("a stage must not start once the budget is exhausted")

    stages = {s.name: s.status for s in recorder.manifest.stages}
    assert stages == {"plan": "succeeded", "build": "failed"}
    assert recorder.manifest.status == "failed"


def test_a_warning_reaches_the_event_log_and_the_summary(tmp_path: Path) -> None:
    ctx = _context(tmp_path, **{"budget.max_tokens": 1000})
    recorder = RunRecorder.begin(ctx, request=REQUEST)

    with recorder.running():
        ctx.events.emit(
            "llm.call",
            model="openai/gpt-6-luna",
            usage={"prompt_tokens": 850, "completion_tokens": 0},
        )

    events = list(read_events(ctx.run_dir / "events.jsonl"))
    warning = next(e for e in events if e.type == "budget.warning")
    assert warning.data["limit"] == "max_tokens"
    assert recorder.manifest.status == "succeeded"
    assert recorder.manifest.summary.budget_status.state == "warning"  # type: ignore[union-attr]
    assert "Budget: warning (max_tokens 85%)" in format_summary(recorder.manifest.summary)  # type: ignore[arg-type]


# -- through the CLI ------------------------------------------------------------------------


class _SpendingTeam:
    """A stand-in crew that reports model usage the way the bridge would."""

    tokens: tuple[int, int] = (1200, 300)
    model = "openai/gpt-6-luna"

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    def crew(self):  # type: ignore[no-untyped-def]
        ctx, (prompt, completion), model = self.ctx, self.tokens, self.model

        class _Crew:
            def kickoff(self, inputs: dict[str, str]) -> None:
                ctx.events.emit(
                    "llm.call",
                    model=model,
                    usage={"prompt_tokens": prompt, "completion_tokens": completion},
                )
                ctx.events.emit("tool.call", tool="Project Tree")

        return _Crew()


def test_a_fake_run_prints_exact_token_counts_and_a_cost(capsys, monkeypatch) -> None:
    monkeypatch.setattr(main, "EngineeringTeam", _SpendingTeam)

    assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 0

    out = capsys.readouterr().out
    assert (
        "Usage: 1,500 tokens (prompt 1,200, of which 0 cached; completion 300) "
        "in 1 model call(s) and 1 tool call(s)" in out
    )
    assert "Estimated cost: $0.0003" in out  # 1200 x $0.10/M + 300 x $0.50/M


def test_a_fake_run_with_an_unpriced_model_prints_unknown_never_zero(capsys, monkeypatch) -> None:
    class Unpriced(_SpendingTeam):
        model = "someone/new-model"

    monkeypatch.setattr(main, "EngineeringTeam", Unpriced)

    assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 0

    out = capsys.readouterr().out
    assert "Estimated cost: unknown (no price for someone/new-model" in out
    assert "$0" not in out


def test_the_cli_leaves_usage_and_summary_in_the_run_directory(monkeypatch) -> None:
    monkeypatch.setattr(main, "EngineeringTeam", _SpendingTeam)
    assert main.run(["--request", REQUEST, "--project-name", "demo"]) == 0

    store = RunStore(Path.cwd() / "workspace" / "demo")
    manifest: RunManifest = store.list_runs()[0]
    usage = json.loads((store.run_dir(manifest.run_id) / "usage.json").read_text("utf-8"))
    assert usage["totals"]["total_tokens"] == 1500 and usage["estimated_cost_usd"] == pytest.approx(
        0.00027
    )
    assert manifest.summary is not None and manifest.summary.estimated_cost_usd == pytest.approx(
        0.00027
    )


def test_a_budget_stop_in_the_cli_is_a_clean_failure(capsys, monkeypatch) -> None:
    monkeypatch.setattr(main, "EngineeringTeam", _SpendingTeam)
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_TOKENS", "1000")

    code = main.run(["--request", REQUEST, "--project-name", "demo"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Engineering team run stopped: Budget exceeded: max_tokens" in captured.err
    assert "Traceback" not in captured.err
    assert "Budget: exceeded" in captured.out
    manifest = RunStore(Path.cwd() / "workspace" / "demo").list_runs()[0]
    assert manifest.status == "failed"


def test_prepare_only_prints_no_usage_report(capsys) -> None:
    assert main.run(["--request", REQUEST, "--project-name", "demo", "--prepare-only"]) == 0

    assert "Usage:" not in capsys.readouterr().out
