"""Prompt sizes per stage session (T24): what a session starts with against the model's context
window, the warning above 60 %, and the rows in the manifest and the report."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from report_helpers import run_dir_in, write_events, write_manifest

from engineering_team.contracts import Event
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.report import write_report
from engineering_team.report.collect import build_report
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.prompt_sizes import (
    PromptSize,
    announce,
    measure,
    prompt_rows,
    render,
    tool_chars,
)
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM

MakeContext = Callable[..., RunContext]
ROW = {
    "stage": "implement",
    "agent": "backend_engineer",
    "lane": None,
    "model": "openai/gpt-6.1",
    "always_on": 300,
    "pack": 2400,
    "task": 700,
    "tools": 1600,
    "start": 5000,
    "history": 12_000,
    "window": 8000,
    "window_source": "settings",
    "fraction": 0.625,
    "warned": True,
}


def event(seq: int, kind: str, stage: str = "implement", **data: object) -> Event:
    from datetime import UTC, datetime

    return Event(
        seq=seq,
        ts=datetime(2026, 11, 1, tzinfo=UTC),
        run_id="r",
        type=kind,
        stage=stage,
        agent=str(data.pop("agent", "backend_engineer")),
        lane=data.pop("lane", None),  # type: ignore[arg-type]
        data=data,
    )


# -- measuring -------------------------------------------------------------------------------------


def test_placeholders_are_filled_the_way_crewai_does() -> None:
    inputs = {"requirements": "Build it.", "handoff": "H" * 8}

    text = render("Task {requirements} / {handoff} / {unknown} / {{literal}}", inputs)

    assert text == "Task Build it. / HHHHHHHH / {unknown} / {{literal}}"


def test_a_session_is_sized_in_tokens_of_four_characters_by_part() -> None:
    schema = SimpleNamespace(model_json_schema=lambda: {"type": "object", "x": "y" * 40})
    tool = SimpleNamespace(name="T" * 10, description="d" * 50, args_schema=schema)
    inputs = {"handoff": "h" * 400, "context": "c" * 200, "requirements": "r" * 100}

    size = measure(
        role="r" * 40,
        goal="g" * 40,
        backstory="b" * 320,
        description="Do {requirements}. {handoff} {context}",
        inputs=inputs,
        tools=[tool],
        window=10_000,
        window_source="settings",
    )

    assert size.always_on == 100
    assert size.pack == 150  # the hand-off and the project context
    assert 25 <= size.task <= 27  # the request, and the words around the placeholders
    assert size.tools == (60 + len('{"type": "object", "x": "' + "y" * 40 + '"}')) // 4
    assert size.start == size.always_on + size.pack + size.task + size.tools
    assert size.fraction == size.start / 10_000


def test_a_tool_without_a_dumpable_schema_is_counted_without_it() -> None:
    broken = SimpleNamespace(
        name="Tool",
        description="Does a thing.",
        args_schema=SimpleNamespace(model_json_schema=None),
    )

    assert tool_chars(broken) == len("Tool") + len("Does a thing.")
    assert tool_chars(SimpleNamespace()) == 0


def test_an_unknown_window_never_divides_by_zero() -> None:
    size = PromptSize(10, 10, 10, 10, window=0, window_source="unknown")

    assert size.fraction == 0.0


# -- announcing ------------------------------------------------------------------------------------


def test_a_session_below_the_share_is_recorded_without_a_warning(make_context: MakeContext) -> None:
    ctx = make_context()

    warning = announce(
        ctx, "implement", "backend_engineer", "m", PromptSize(100, 500, 200, 200, 4000, "settings")
    )

    assert warning is None
    (sized,) = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "context.sized"]
    assert sized.stage == "implement" and sized.agent == "backend_engineer"
    assert (
        sized.data["pack"] == 500 and sized.data["window"] == 4000 and sized.data["warned"] is False
    )
    assert not [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "context.warning"]


def test_a_session_above_sixty_percent_of_the_window_warns(make_context: MakeContext) -> None:
    ctx = make_context()
    size = PromptSize(300, 2400, 700, 1600, window=8000, window_source="settings")  # 62.5 %

    warning = announce(ctx, "implement", "backend_engineer", "openai/gpt-6.1", size)

    assert warning is not None and "62%" in warning and "8,000-token" in warning
    assert "context.stage_tokens" in warning
    types = [e.type for e in read_events(ctx.run_dir / "events.jsonl")]
    assert types.count("context.sized") == 1 and types.count("context.warning") == 1


def test_exactly_the_share_does_not_warn_and_the_share_is_a_setting(
    make_context: MakeContext,
) -> None:
    at_the_line = PromptSize(600, 0, 0, 0, window=1000, window_source="settings")

    assert announce(make_context("a"), "s", "t", "m", at_the_line) is None
    strict = make_context("b", settings=load_settings(overrides={"context.warn_fraction": 0.5}))
    assert announce(strict, "s", "t", "m", at_the_line) is not None


# -- a real stage session --------------------------------------------------------------------------


def run_stage(ctx: RunContext, state: PipelineState | None = None) -> None:
    stage = StageSpec(name="implement", teammates=["backend_engineer"])
    request = StageRequest(
        ctx=ctx,
        stage=stage,
        teammate="backend_engineer",
        state=state or PipelineState(),
        requirements="Build a notes CLI.",
        handoff="Hand-off from the controller:\n" + "- a file\n" * 50,
    )
    CrewStageRunner(llm_factory=lambda key: ScriptedLLM(["done"])).run(request)


def test_a_stage_session_records_its_sizes_before_the_first_model_call(
    make_context: MakeContext,
) -> None:
    ctx = make_context()

    run_stage(ctx)

    (sized,) = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "context.sized"]
    assert (sized.stage, sized.agent) == ("implement", "backend_engineer")
    data = sized.data
    assert data["always_on"] > 50 and data["tools"] > 100 and data["task"] > 20
    assert data["pack"] >= len("Hand-off from the controller:\n" + "- a file\n" * 50) // 4
    assert data["window"] == 1_050_000 and data["window_source"] == "model facts"
    assert data["warned"] is False


def test_a_model_without_known_facts_uses_the_window_crewai_reports(
    make_context: MakeContext,
) -> None:
    unknown = {"models.roles.backend_engineer.model": "openai/not-in-the-facts-table"}
    ctx = make_context(settings=load_settings(overrides=unknown))

    run_stage(ctx)

    (sized,) = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "context.sized"]
    assert sized.data["window"] == 1_000_000 and sized.data["window_source"] == "crewai"


def test_a_small_window_makes_the_stage_warn_and_still_run(make_context: MakeContext) -> None:
    ctx = make_context(
        settings=load_settings(
            overrides={
                "context.warn_fraction": 0.001,  # 1,000 of the scripted model's million tokens
                "models.tiers.worker.context_window": 4096,
            }
        )
    )

    run_stage(ctx)

    types = [e.type for e in read_events(ctx.run_dir / "events.jsonl")]
    assert "context.warning" in types and "context.sized" in types


def test_a_measurement_that_fails_never_stops_the_stage(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.pipeline import stage_size

    ctx = make_context()

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("cannot measure")

    monkeypatch.setattr(stage_size, "measure", broken)

    run_stage(ctx)

    errors = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "context.error"]
    assert errors and "cannot measure" in errors[0].data["error"]


# -- the rows --------------------------------------------------------------------------------------


def test_rows_join_each_session_with_the_most_history_its_calls_carried() -> None:
    log = [
        event(
            1,
            "context.sized",
            model="m",
            always_on=100,
            pack=200,
            task=50,
            tools=150,
            window=1000,
            window_source="settings",
            warned=False,
        ),
        event(
            2,
            "llm.request",
            prompt_chars={"system": 900, "tools": 600, "history": 400, "results": 0},
        ),
        event(
            3,
            "llm.request",
            prompt_chars={"system": 900, "tools": 600, "history": 2000, "results": 1200},
        ),
        event(
            4,
            "llm.request",
            prompt_chars={"system": 900, "tools": 600, "history": 800, "results": 0},
        ),
        event(
            5,
            "context.sized",
            stage="review",
            agent="qa_engineer",
            model="m",
            always_on=10,
            pack=0,
            task=10,
            tools=0,
            window=0,
            window_source="unknown",
            warned=False,
        ),
        event(
            6,
            "context.sized",
            model="m",
            always_on=100,
            pack=900,
            task=50,
            tools=150,
            window=1000,
            window_source="settings",
            warned=True,
        ),
        event(7, "llm.request", prompt_chars={"history": 40, "results": 0}),
        event(
            8, "llm.request", stage="review", agent="qa_engineer", prompt_chars={"history": 8000}
        ),
    ]

    first, review, second = prompt_rows(reversed(log))  # any order in: the log's order out

    assert (first["stage"], first["start"], first["fraction"], first["history"]) == (
        "implement",
        500,
        0.5,
        800,
    )
    assert (review["stage"], review["fraction"], review["history"]) == ("review", 0.0, 2000)
    assert (second["start"], second["history"], second["warned"]) == (1200, 10, True)


def test_a_lane_is_a_session_of_its_own() -> None:
    log = [
        event(
            1, "context.sized", lane=1, model="m", always_on=1, pack=1, task=1, tools=1, window=100
        ),
        event(
            2, "context.sized", lane=2, model="m", always_on=2, pack=2, task=2, tools=2, window=100
        ),
        event(3, "llm.request", lane=2, prompt_chars={"history": 4000}),
    ]

    one, two = prompt_rows(log)

    assert (one["lane"], one["history"]) == ("1", 0) and (two["lane"], two["history"]) == (
        "2",
        1000,
    )
    assert prompt_rows([]) == []


# -- the manifest and the report -------------------------------------------------------------------


def test_the_finished_run_keeps_the_rows_in_its_manifest(make_context: MakeContext) -> None:
    from engineering_team.runtime.run_store import RunStore
    from engineering_team.runtime.session import RunRecorder

    ctx = make_context()
    recorder = RunRecorder.begin(ctx, request="Build a notes CLI.")

    with recorder.running(), recorder.stage("implement"):
        run_stage(ctx)

    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    (row,) = manifest.prompts
    assert row["stage"] == "implement" and row["agent"] == "backend_engineer"
    assert row["start"] == row["always_on"] + row["pack"] + row["task"] + row["tools"]
    assert row["window"] == 1_050_000 and row["warned"] is False


def test_the_report_shows_the_prompt_sizes_and_warns_about_a_crowded_start(tmp_path) -> None:  # type: ignore[no-untyped-def]
    directory = run_dir_in(tmp_path)
    write_manifest(
        directory, prompts=[ROW, {**ROW, "stage": "review", "warned": False, "fraction": 0.2}]
    )
    write_events(directory, [(0, "run.started", {})])

    report = build_report(directory)
    text = render_markdown(report)
    html = write_report(directory, "html").read_text(encoding="utf-8")

    assert [n.text for n in report.warnings if "context window" in n.text] == [
        "Stage implement (backend_engineer) started at 62% of openai/gpt-6.1's 8,000-token context "
        "window (5,000 tokens); the history comes on top. Lower context.stage_tokens, narrow the "
        "request, or use a model with a larger window."
    ]
    assert "## Prompt sizes" in text
    assert "| implement | backend_engineer | 300 | 2,400 | 700 | 1,600 | 5,000 | 12,000 |" in text
    assert "| 8,000 | 62% |" in text
    assert '<section id="prompts">' in html and 'href="#prompts"' in html and "12,000" in html


def test_a_run_from_before_prompt_sizes_has_no_such_section(tmp_path) -> None:  # type: ignore[no-untyped-def]
    directory = run_dir_in(tmp_path)
    write_manifest(directory)
    write_events(directory, [(0, "run.started", {})])

    text = render_markdown(build_report(directory))
    html = write_report(directory, "html").read_text(encoding="utf-8")

    assert "Prompt sizes" not in text and 'id="prompts"' not in html
