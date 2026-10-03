"""The real stage runner: one small CrewAI crew per stage, on ScriptedLLM, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pipeline_fakes import BODY, PLAN, REQUEST, SPEC
from test_pipeline_flow import ROOT, only_run, project, run_dir

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.contracts import Plan, Spec, WorkPackage
from engineering_team.pipeline import strategies
from engineering_team.pipeline.recipes import StageSpec, load_recipe
from engineering_team.pipeline.stages import (
    CrewStageRunner,
    StageError,
    StageRequest,
    stage_groups,
    teammate_for,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.testing import ScriptedLLM, ToolCall

DOC = "# Doc\n" + BODY


def write(path: str, content: str = DOC) -> ToolCall:
    return ToolCall("Write Project File", {"path": path, "content": content})


def scripts() -> dict[str, ScriptedLLM]:
    """One scripted model per teammate; a teammate's script covers its stages in recipe order."""

    return {
        "solution_architect": ScriptedLLM(
            [
                SPEC.model_dump_json(),
                write("docs/architecture.md"),
                PLAN.model_dump_json(),
            ]
        ),
        "backend_engineer": ScriptedLLM(
            [
                write("README.md"),
                "Foundation is in place.",
                write("src/storage/main.py", "VALUE = 1\n"),
                "WP-1 delivered AC-1.",
                write("src/cli/main.py", "VALUE = 2\n"),
                "WP-2 delivered AC-2.",
                write("docs/integration.md"),
                "Integrated.",
            ]
        ),
        "quality_engineer": ScriptedLLM(
            [
                write("docs/verification.md"),
                "Everything verified.",
                write("docs/release-report.md"),
                "Released.",
            ]
        ),
    }


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> dict[str, ScriptedLLM]:
    llms = scripts()
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )
    return llms


def run_cli(*extra: str) -> int:
    return main.run(
        [
            "--request",
            REQUEST,
            "--project-name",
            "demo",
            "--workspace-root",
            str(Path.cwd() / ROOT),
            "--strategy",
            "pipeline",
            *extra,
        ]
    )


def test_real_crews_on_scripted_models_build_the_project_stage_by_stage(
    models: dict[str, ScriptedLLM],
) -> None:
    assert run_cli() == 0

    for llm in models.values():
        llm.assert_exhausted()
    manifest = only_run()
    assert manifest.status == "succeeded"
    assert [(r.name, r.status) for r in manifest.stages] == [
        ("spec", "succeeded"),
        ("plan", "succeeded"),
        ("foundation", "succeeded"),
        ("implement", "succeeded"),
        ("integrate", "succeeded"),
        ("verify", "succeeded"),
        ("release", "succeeded"),
    ]
    for path in (
        "docs/architecture.md",
        "README.md",
        "src/storage/main.py",
        "docs/release-report.md",
    ):
        assert (project() / path).is_file(), path
    state = PipelineState.load(run_dir(manifest))
    assert state is not None and state.spec == SPEC
    assert state.plan is not None and len(state.plan.work_packages) == 2
    assert state.summaries["foundation"] == "Foundation is in place."


def test_each_stage_agent_sees_its_task_the_contracts_and_its_card(
    models: dict[str, ScriptedLLM],
) -> None:
    run_cli()

    architect = models["solution_architect"].calls
    assert "tiny notes CLI" in architect[0].prompt  # the request, interpolated
    assert "Do not write files" in architect[0].prompt
    plan_prompt = architect[1].prompt
    assert "Notes CLI" in plan_prompt and "AC-1" in plan_prompt  # the spec JSON from stage 1
    assert "Your task board card is K-002" in plan_prompt
    backend = models["backend_engineer"].calls
    implement_prompt = next(call.prompt for call in backend if "Your work package" in call.prompt)
    assert "WP-1" in implement_prompt and "src/storage/**" in implement_prompt
    assert '"role": "backend"' in implement_prompt
    # Role text comes from agents.yaml, not from the stage prompts.
    assert "Backend and platform engineer for demo" in backend[0].messages[0]["content"]


def test_stage_agents_get_the_coordination_and_development_tools(
    models: dict[str, ScriptedLLM],
) -> None:
    run_cli()

    first = models["backend_engineer"].calls[0]
    assert first.tools is not None
    names = {tool["function"]["name"] for tool in first.tools}

    assert {
        "write_project_file",  # fs_write
        "search_project_files",  # search
        "run_project_command",  # command
        "list_board_cards",  # board
        "report_progress",
        "ask_human",  # human
        "write_note",  # notes
    } <= names
    assert not any(name.startswith("web_") or name.startswith("browser_") for name in names)


def test_token_usage_is_attributed_to_the_stage_that_spent_it(
    models: dict[str, ScriptedLLM],
) -> None:
    run_cli()

    usage = json.loads((run_dir(only_run()) / "usage.json").read_text(encoding="utf-8"))

    assert set(usage["by_stage"]) == {
        "spec",
        "plan",
        "foundation",
        "implement",
        "integrate",
        "verify",
        "release",
    }
    assert usage["totals"]["calls"] == 15  # every scripted turn was one model call
    assert usage["by_agent"]
    events = list(read_events(run_dir(only_run()) / "events.jsonl"))
    assert {e.stage for e in events if e.type == "llm.call"} == set(usage["by_stage"])


def test_the_board_shows_every_stage_done_and_the_packages_the_architect_planned(
    models: dict[str, ScriptedLLM],
) -> None:
    run_cli()

    board = BoardStore(run_dir(only_run()))

    assert [c.status for c in board.cards(kind="stage")] == ["done"] * 7
    assert [(c.title, c.assignee) for c in board.cards(kind="work_package")] == [
        ("WP-1: Storage", "backend_engineer"),
        ("WP-2: CLI", "backend_engineer"),
    ]


def test_a_final_answer_that_is_not_the_contract_fails_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = scripts()
    # CrewAI itself asks the model to convert a non-JSON answer a few times before giving up.
    llms["solution_architect"] = ScriptedLLM(["I will write a spec."] * 40)
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )

    code = run_cli()

    assert code == 1
    manifest = only_run()
    detail = next(r for r in manifest.stages if r.name == "spec").detail
    # Either CrewAI's own conversion error or ours says the answer was not a valid Spec.
    assert "valid Spec" in detail or "Pydantic model" in detail
    # The stage's one retry carried the failure to the agent.
    assert any("previous attempt failed" in c.prompt for c in llms["solution_architect"].calls)


def test_the_stage_runner_rejects_unknown_teammates_and_stages(
    make_context,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    ctx = make_context()
    runner = CrewStageRunner(llm_factory=lambda key: ScriptedLLM(["x"]))
    state = PipelineState()

    ghost = StageSpec(name="spec", teammates=["ghost_engineer"], outputs=["spec"])
    with pytest.raises(StageError, match="Unknown teammate 'ghost_engineer'"):
        runner.run(StageRequest(ctx=ctx, stage=ghost, teammate="ghost_engineer", state=state))

    unprompted = StageSpec(name="mystery", teammates=["backend_engineer"])
    with pytest.raises(StageError, match="No prompt for stage 'mystery'"):
        runner.run(
            StageRequest(ctx=ctx, stage=unprompted, teammate="backend_engineer", state=state)
        )

    twice = StageSpec(name="spec", teammates=["backend_engineer"], outputs=["spec", "plan"])
    with pytest.raises(StageError, match="several contracts"):
        runner.run(StageRequest(ctx=ctx, stage=twice, teammate="backend_engineer", state=state))


def test_a_work_package_goes_to_the_teammate_its_role_names() -> None:
    stage = load_recipe("new").stage("implement")

    def role(name: str) -> str:
        package = WorkPackage(id="WP", title="t", role=name)
        return teammate_for(stage, package)

    assert role("backend") == "backend_engineer"
    assert role("Frontend") == "frontend_engineer"
    assert role("frontend_engineer") == "frontend_engineer"
    assert role("front end") == "frontend_engineer"
    assert role("devops") == "backend_engineer"  # no such teammate yet: the first listed
    assert teammate_for(stage, None) == "backend_engineer"


def test_tool_groups_follow_the_default_table(make_context) -> None:  # type: ignore[no-untyped-def]
    ctx = make_context()

    groups = stage_groups(ctx, "backend_engineer")

    assert set(groups) >= {
        "fs_read", "fs_write", "search", "command", "dev", "runtime",
        "code_intel", "board", "notes", "human", "web",
    }  # fmt: skip
    assert "browser" not in groups
    assert set(Spec.model_fields) and set(Plan.model_fields)  # contracts imported for the prompts
