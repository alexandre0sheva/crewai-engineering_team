"""Repo memory across whole runs (T19): an agent's proposal, and a second run that does not
rediscover what the first one verified."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from repo_fixtures import make_repo
from test_feature_mode import git, latest
from test_fix_mode import BUGGY, fix_runner, run_fix, work
from test_pipeline_crews import run_cli, scripts
from test_pipeline_flow import only_run, run_dir
from test_repo_memory import fact
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.memory import MemoryStore
from engineering_team.modes.isolation import RECORD, Isolation
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.trajectory import read_trajectory
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall
from engineering_team.tools import build_tools
from engineering_team.tools.workspace import ProjectWorkspace

runner = CliRunner()


# -- an agent proposes a fact through the tool -------------------------------------------------


def test_an_agent_proposes_a_fact_and_a_verified_run_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_LEAN_SIZE", "large")
    monkeypatch.setenv("ENGINEERING_LEAN_CEREMONY", "always")
    llms = scripts()
    proposal = ToolCall(
        "Use Tool",
        {
            "name": "Remember Fact",
            "arguments": {
                "kind": "gotcha",
                "text": "The README is where the project's overview lives.",
                "paths": "README.md",
            },
        },
    )
    script = list(llms["backend_engineer"]._script)
    script[1:1] = [ToolCall("Read Project File", {"path": "README.md"}), proposal]
    llms["backend_engineer"] = ScriptedLLM(script)  # type: ignore[arg-type]
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )

    assert run_cli() == 0

    manifest = only_run()
    directory = run_dir(manifest)
    store = MemoryStore(directory.parents[2])
    [kept] = store.facts()
    assert kept.kind == "gotcha" and kept.status == "verified"
    assert kept.proposed_by == "backend_engineer" and kept.evidence.run_id == manifest.run_id
    assert kept.evidence.tool_seqs  # the agent's read of README.md, in the trajectory
    calls = [
        e for e in read_trajectory(directory / "trajectory.jsonl") if e.tool == "Remember Fact"
    ]
    assert len(calls) == 1 and calls[0].output.startswith("Proposed fact")
    types = [e.type for e in read_events(directory / "events.jsonl")]
    assert "memory.recorded" in types
    shown = runner.invoke(app, ["memory", "list", "--repo", str(directory.parents[2])])
    assert shown.exit_code == 0 and kept.id in shown.output
    assert "The README is where the project's overview lives." in shown.output


# -- a worktree or a copy starts from the memory of its source ---------------------------------


def test_a_run_in_a_worktree_starts_from_the_sources_memory_and_never_writes_to_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    work_tree = tmp_path / "ws" / "copy"
    (source / ".engineering-team" / "memory").mkdir(parents=True)
    work_tree.mkdir(parents=True)
    source_store = MemoryStore(source)
    source_store.upsert(fact("seed", "Seeded by an earlier run."))
    record = Isolation(mode="worktree", source=source, workspace=work_tree).to_json()
    (work_tree / RECORD).parent.mkdir(parents=True, exist_ok=True)
    (work_tree / RECORD).write_text(json.dumps(record))

    ctx = RunContext.create(load_settings(), ProjectWorkspace.create(work_tree))
    ctx.memory.upsert(fact("learned", "Learned in the worktree."))

    assert sorted(f.topic for f in ctx.memory.facts()) == ["learned", "seed"]
    assert [f.topic for f in source_store.facts()] == ["seed"]  # the source is untouched


# -- the second run does not rediscover --------------------------------------------------------


FACT = "CONTRIBUTING requires `make test` to pass before any change is sent."


def discovering(
    on_call: Callable[[StageRequest], None], informed: list[bool]
) -> Callable[[StageRequest], None]:
    """The scripted debugger. Told nothing about how the project is tested, it finds out (a few
    reads and searches) and proposes what it learned; told the verified fact, it goes straight
    to running the tests."""

    def call(request: StageRequest) -> None:
        if request.stage.name == "reproduce":
            tools = {t.name: t for t in build_tools(request.ctx, agent="debugger", lazy=False)}
            known = "CONTRIBUTING requires" in request.handoff
            informed.append(known)
            if not known:
                tools["List Scripts"].run(path=".")
                tools["Read Project File"].run(path="pyproject.toml")
                tools["Read Project File"].run(path="Makefile")
                tools["Read Project File"].run(path="CONTRIBUTING.md")
                tools["Search Project Files"].run(pattern="pytest")
                tools["Remember Fact"].run(kind="convention", text=FACT, paths="CONTRIBUTING.md")
            tools["Run Project Command"].run(command="pytest -q tests/test_store.py")
        on_call(request)

    return call


def tool_calls(directory: Path) -> int:
    return sum(1 for e in read_trajectory(directory / "trajectory.jsonl") if e.kind == "tool")


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_second_run_on_the_same_repository_skips_rediscovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    start = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    informed: list[bool] = []
    counts: list[int] = []

    for _ in range(2):
        result, _scripted = run_fix(
            repo, monkeypatch, fake=fix_runner(discovering(work(), informed))
        )
        assert result.exit_code == 0, result.output
        counts.append(tool_calls(latest().run_dir))
        git(repo, "checkout", "-q", start)  # back to the buggy tree; memory is not part of it

    assert informed == [False, True]
    assert counts[1] < counts[0], counts
    facts = MemoryStore(repo).facts()
    [learned] = [f for f in facts if f.text == FACT]  # proposed in run 1, kept by the controller
    assert learned.status == "verified" and learned.proposed_by == "debugger"
    assert learned.evidence.tool_seqs and [s.path for s in learned.sources] == ["CONTRIBUTING.md"]
    commands = [f for f in facts if f.kind == "command"]
    assert commands and all(f.evidence.check_ids for f in commands)  # the baseline's own record
    assert any("make test" in f.text for f in commands)  # as the project names it, not as run
    events = read_events(latest().run_dir / "events.jsonl")
    injected = [e for e in events if e.type == "memory.injected"]
    assert any(learned.id in e.data["facts"] for e in injected)  # the log says what it was given
