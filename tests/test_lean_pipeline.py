"""The lean pipeline end to end on scripted models: what a small task runs, and what it skips."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from perf.scenarios import install, lean_scripts, run_new
from pipeline_fakes import PLAN
from test_pipeline_flow import only_run, project, run_dir

from engineering_team.contracts import Plan, ReviewReport
from engineering_team.pipeline.stages import CrewStageRunner, StageOutput, StageRequest, prompts
from engineering_team.runtime.events import read_events

VOLATILE = ("{handoff}", "{card}", "{notes}", "{resume_note}")


def events(kind: str) -> list[dict[str, Any]]:
    return [e.data for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == kind]


def test_a_small_new_project_runs_design_one_build_and_one_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = lean_scripts()
    install(monkeypatch, llms)

    assert run_new("--strategy", "pipeline") == 0

    records = {record.name: record for record in only_run().stages}
    assert [name for name, r in records.items() if r.status == "succeeded"] == [
        "design", "implement", "verify", "review", "release_notes",
    ]  # fmt: skip
    skipped = {name: r.detail for name, r in records.items() if r.status == "skipped"}
    assert skipped == {
        "spec": "skipped: not_large_task",
        "plan": "skipped: not_large_task",
        "foundation": "skipped: single_package",
        "integrate": "skipped: single_package",
        "devops": "skipped: devops_not_requested",
        "docs": "skipped: docs_not_requested",
    }
    (profile,) = events("lean.profile")
    assert profile["size"] == "small" and profile["ceremony"] == []
    (collapsed,) = events("plan.collapsed")
    assert collapsed["packages"] == ["WP-1", "WP-2"]
    (reviewers,) = events("lean.reviewers")
    assert reviewers["kept"] == ["code_reviewer"] and reviewers["left_out"] == ["security_engineer"]


def test_the_controller_writes_spec_plan_and_release_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, lean_scripts())

    assert run_new("--strategy", "pipeline") == 0

    docs = project() / "docs"
    assert (docs / "spec.md").read_text().startswith("# Notes CLI")
    plan = (docs / "plan.md").read_text()
    assert "### WP-1: Build the whole plan" in plan
    assert "Owns: src/storage/**, src/cli/**." in plan
    assert "- WP-2 CLI (src/cli/**)" in plan
    notes = (docs / "release-report.md").read_text()
    assert notes.startswith("# Notes CLI: release notes")
    assert "Verdict: verified." in notes and "**AC-1**" in notes
    assert "not production readiness" in notes


def test_every_session_starts_with_a_hand_off_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    llms = lean_scripts()
    install(monkeypatch, llms)

    assert run_new("--strategy", "pipeline") == 0

    build = llms["backend_engineer"].calls[0].prompt
    assert "Hand-off from the controller:" in build
    assert "Earlier stages, in their agents' own words (not evidence):" in build
    review = llms["code_reviewer"].calls[0].prompt
    assert re.search(r"- src/cli/main\.py  \d+ B  [0-9a-f]{8}", review)
    assert "The controller's latest check results:\n- tests: passed" in review


def test_the_lean_design_and_the_small_review_run_on_the_worker_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = lean_scripts()
    seen: list[StageRequest] = []
    real = CrewStageRunner(llm_factory=lambda key: llms[key])

    class Recording:
        def run(self, request: StageRequest) -> StageOutput:
            seen.append(request)
            return real.run(request)

    from engineering_team.pipeline import strategies

    monkeypatch.setattr(strategies, "CrewStageRunner", Recording)

    assert run_new("--strategy", "pipeline") == 0

    tiers = {request.stage.name: request.tier for request in seen}
    assert tiers == {"design": "worker", "implement": None, "review": "worker"}


def test_a_request_for_ci_and_docs_runs_that_ceremony(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_pipeline_crews import write

    from engineering_team.testing import ScriptedLLM

    llms = lean_scripts()
    llms["devops_engineer"] = ScriptedLLM([write("docs/devops.md"), "CI added."])
    llms["technical_writer"] = ScriptedLLM([write("docs/usage.md"), "Documented."])
    install(monkeypatch, llms)
    monkeypatch.setattr(
        "perf.scenarios.REQUEST",
        "Build a tiny notes CLI with add and list commands, GitHub Actions CI and a user guide.",
    )

    assert run_new("--strategy", "pipeline") == 0

    succeeded = [r.name for r in only_run().stages if r.status == "succeeded"]
    assert {"devops", "docs"} <= set(succeeded)


def test_a_three_package_plan_is_medium_and_is_not_collapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_pipeline_crews import write

    from engineering_team.contracts import WorkPackage
    from engineering_team.testing import ScriptedLLM

    third = WorkPackage(
        id="WP-3", title="Export", role="backend", owned_paths=["src/export/**"],
        criteria_ids=["AC-2"],
    )  # fmt: skip
    plan = Plan(stack="python", work_packages=[*PLAN.work_packages, third])
    llms = lean_scripts(plan)
    llms["backend_engineer"] = ScriptedLLM(
        [
            write("README.md"),
            "Foundation.",
            write("src/storage/main.py", "A = 1\n"),
            "WP-1.",
            write("src/cli/main.py", "B = 1\n"),
            "WP-2.",
            write("src/export/main.py", "C = 1\n"),
            "WP-3.",
            write("docs/integration.md"),
            "Integrated.",
        ]  # fmt: skip
    )
    llms["security_engineer"] = ScriptedLLM(
        [ReviewReport(summary="Nothing found.").model_dump_json()]
    )
    install(monkeypatch, llms)

    assert run_new("--strategy", "pipeline") == 0

    assert not events("plan.collapsed")
    succeeded = [r.name for r in only_run().stages if r.status == "succeeded"]
    assert succeeded[:4] == ["design", "foundation", "implement", "integrate"]


def test_changing_parts_come_last_in_every_prompt() -> None:
    for key, prompt in prompts().items():
        text = prompt["description"]
        stable = [m.end() for m in re.finditer(r"\{[a-z_]+\}", text) if m.group() not in VOLATILE]
        volatile = [text.index(name) for name in VOLATILE if name in text]
        if stable and volatile:
            assert max(stable) < min(volatile), key


def test_a_lean_run_resumes_after_its_skipped_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline_fakes import FakeRunner
    from test_pipeline_flow import resume, start, use_runner

    use_runner(monkeypatch, FakeRunner(fail={"implement:WP-1": 2}))
    assert start() == 1
    failed = only_run()
    assert {r.name: r.status for r in failed.stages}["spec"] == "skipped"

    runner = use_runner(monkeypatch, FakeRunner())
    assert resume(failed.run_id) == 0

    assert runner.calls[0] == ("implement", "WP-1")  # the design is not done again
    assert ("design", None) not in runner.calls
    assert only_run().status == "succeeded"


@pytest.mark.git
def test_a_small_feature_designs_in_one_session_and_skips_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_fixtures import make_repo
    from test_feature_mode import LEGACY, latest, run_feature

    repo = make_repo(tmp_path / "legacy", LEGACY)
    monkeypatch.setenv("ENGINEERING_STRATEGY", "pipeline")  # the feature recipe's own structure

    result, fake = run_feature(repo, monkeypatch)

    assert result.exit_code == 0, result.output
    records = {r.name: r for r in latest().manifest.stages}
    assert [name for name, r in records.items() if r.status == "succeeded"] == [
        "profile", "baseline", "design", "implement", "tests", "verify", "review", "summary",
    ]  # fmt: skip
    assert records["map"].detail == "skipped: small_task"
    assert records["spec"].detail == records["impact"].detail == "skipped: not_large_task"
    assert [call for call in fake.calls if call[0] == "review"] == [("review", None)]
    assert (latest().run_dir / "reports" / "plan.md").is_file()  # not in the user's project
