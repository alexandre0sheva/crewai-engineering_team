"""Scripted scenarios for the offline performance proxies (no model, no network).

Each scenario runs the real stage runner (real agents, real tools, real prompts) on ScriptedLLM
and reports what the run cost in the units the performance targets are written in: agent
sessions, model calls, tool-schema tokens per agent, always-on prompt size, ceremony stages.
Those numbers do not depend on the model's wording, so they only move when the structure of the
team does (docs/IMPLEMENTATION_PLAN_0.3.0.md, "Performance targets").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pipeline_fakes import BODY, PLAN, REQUEST, SPEC, write_checks
from test_pipeline_crews import scripts, write
from test_pipeline_flow import ROOT, only_run, run_dir

from engineering_team import main
from engineering_team.contracts import Design, Plan, ReviewReport, RunManifest, WorkPackage
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import CrewStageRunner
from engineering_team.pricing import build_table
from engineering_team.runtime.profile import build_profile
from engineering_team.testing import ScriptedLLM, ToolCall

CEREMONY_STAGES = ("devops", "docs", "release")  # what a small task should not need
DOC = "# Doc\n" + BODY


@dataclass(frozen=True)
class Measured:
    """What one scenario cost."""

    sessions: int
    model_calls: int
    schema_tokens: int  # the largest tool-schema size any agent sent with its calls
    always_on_tokens: int  # the largest system + task prompt any agent sent per call
    ceremony_stages: int
    stage_names: tuple[str, ...]


def install(monkeypatch: pytest.MonkeyPatch, llms: dict[str, ScriptedLLM]) -> None:
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )


def run_new(*extra: str) -> int:
    return main.run(
        [
            "new",
            "--request",
            REQUEST,
            "--project-name",
            "demo",
            "--workspace-root",
            str(Path.cwd() / ROOT),
            *(["--checks", str(write_checks())] if "pipeline" in extra else []),
            *extra,
        ]
    )


def measure(manifest: RunManifest | None = None, directory: Path | None = None) -> Measured:
    """Profile the run that just finished (the only run of the ``demo`` project by default)."""

    manifest = manifest or only_run()
    profile = build_profile(directory or run_dir(manifest), build_table({}))
    schema = max(profile.schema_tokens().values(), default=0)
    always_on = max(
        (s.prompt_parts.get("system", 0) // max(s.calls, 1) for s in profile.sessions), default=0
    )
    names = tuple(record.name for record in manifest.stages if record.status == "succeeded")
    return Measured(
        sessions=len(profile.sessions),
        model_calls=profile.calls,
        schema_tokens=schema,
        always_on_tokens=always_on,
        ceremony_stages=sum(1 for name in names if name in CEREMONY_STAGES),
        stage_names=names,
    )


def lean_scripts(plan: Plan = PLAN) -> dict[str, ScriptedLLM]:
    """The lean team on the notes CLI: one design session, one implementer, one reviewer."""

    return {
        "solution_architect": ScriptedLLM([Design(spec=SPEC, plan=plan).model_dump_json()]),
        "backend_engineer": ScriptedLLM(
            [
                write("README.md"),
                write("src/storage/main.py", "VALUE = 1\n"),
                write("src/cli/main.py", "VALUE = 2\n"),
                "Built the notes CLI: AC-1 and AC-2.",
            ]
        ),
        "debugger": ScriptedLLM([]),  # only called when a check fails
        "code_reviewer": ScriptedLLM([ReviewReport(summary="Reads well.").model_dump_json()]),
    }


def small_new_project_pipeline(monkeypatch: pytest.MonkeyPatch) -> Measured:
    """The default pipeline on a two-package notes CLI: the lean team (design, one build
    session for the collapsed plan, one reviewer, no ceremony)."""

    llms = lean_scripts()
    install(monkeypatch, llms)
    assert run_new("--strategy", "pipeline") == 0
    for llm in llms.values():
        llm.assert_exhausted()
    return measure()


def full_pipeline(monkeypatch: pytest.MonkeyPatch) -> Measured:
    """The same request with every stage (``lean.size = large``, ``lean.ceremony = always``):
    the 0.2.0 structure, still what a large task gets."""

    monkeypatch.setenv("ENGINEERING_LEAN_SIZE", "large")
    monkeypatch.setenv("ENGINEERING_LEAN_CEREMONY", "always")
    install(monkeypatch, scripts())
    assert run_new("--strategy", "pipeline") == 0
    return measure()


def small_new_project_single(monkeypatch: pytest.MonkeyPatch) -> Measured:
    """The one-agent baseline on the same request."""

    llms = {
        "generalist_engineer": ScriptedLLM(
            [
                write("README.md"),
                write("src/notes.py", "VALUE = 1\n"),
                write("docs/release-report.md"),
                "The notes CLI is built.",
            ]
        )
    }
    install(monkeypatch, llms)
    assert run_new("--strategy", "single") == 0
    return measure()


def medium_feature_pipeline(monkeypatch: pytest.MonkeyPatch) -> Measured:
    """The pipeline on a four-package plan, each package depending on the one before: a medium
    task (one design session, foundation, the packages, integration, both reviewers)."""

    def package(number: int) -> WorkPackage:
        return WorkPackage(
            id=f"WP-{number}",
            title=f"Part {number}",
            role="backend",
            owned_paths=[f"src/part{number}/**"],
            depends_on=[f"WP-{number - 1}"] if number > 1 else [],
            criteria_ids=["AC-1"] if number % 2 else ["AC-2"],
        )

    plan = Plan(stack="python", work_packages=[package(n) for n in range(1, 5)])
    llms = lean_scripts(plan)
    work: list[object] = [write("README.md"), "Foundation is in place."]
    for number in range(1, 5):
        work += [write(f"src/part{number}/main.py", f"VALUE = {number}\n"), f"WP-{number} done."]
    work += [write("docs/integration.md"), "Integrated."]
    llms["backend_engineer"] = ScriptedLLM(work)  # type: ignore[arg-type]
    llms["security_engineer"] = ScriptedLLM(
        [ReviewReport(summary="Nothing found.").model_dump_json()]
    )
    install(monkeypatch, llms)
    assert run_new("--strategy", "pipeline") == 0
    return measure()


def small_bugfix_fix_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Measured:
    """``fix`` on a seeded bug in a small repository: no map and no triage (a small task), the
    debugger writes the failing test and fixes it, one reviewer reads the change."""

    from cli_helpers import workspace_root  # noqa: F401
    from repo_fixtures import make_repo
    from test_feature_mode import latest
    from test_fix_mode import BUGGY, FIXED, GOOD, REGRESSION, REPORT
    from typer.testing import CliRunner

    from engineering_team.cli.app import app
    from engineering_team.modes.fix_contracts import FixNote

    repo = make_repo(tmp_path / "notes", BUGGY)
    note = FixNote(
        root_cause="add built a one-element list instead of extending the one it was given.",
        change="add returns the old items followed by the new one.",
        risk="Callers that relied on the old result.",
        files=["app/store.py"],
    )
    llms = {
        "debugger": ScriptedLLM(
            [
                ToolCall("Read Project File", {"path": "app/store.py"}),
                write("tests/test_regression.py", REGRESSION),
                GOOD.model_dump_json(),
                write("app/store.py", FIXED),
                note.model_dump_json(),
            ]
        ),
        "code_reviewer": ScriptedLLM([ReviewReport(summary="Reads well.").model_dump_json()]),
    }
    install(monkeypatch, llms)
    command = ["--workspace-root", str(Path.cwd() / ROOT), "fix", "--repo", str(repo)]
    result = CliRunner().invoke(app, [*command, "--request", REPORT])
    assert result.exit_code == 0, result.output
    for llm in llms.values():
        llm.assert_exhausted()
    ref = latest()
    return measure(ref.manifest, ref.run_dir)
