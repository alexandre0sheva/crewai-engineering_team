"""Best-of-N attempts on an existing repository: the attempts run in copies of it, and the run
ends with the chosen attempt's patch and change summary, measured against the starting commit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from pipeline_fakes import FakeRunner
from repo_fixtures import make_repo
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from test_cli_do import triage_says
from test_feature_mode import LEGACY, PLAN, REQUEST, SPEC, assert_patch_applies, git, state_of

from engineering_team import main
from engineering_team.modes.change_report import PATCH_FILE, SUMMARY_FILE
from engineering_team.modes.isolation import read_isolation
from engineering_team.pipeline import select as select_module
from engineering_team.pipeline.attempts_contracts import AttemptsRecord
from engineering_team.pipeline.attempts_run import attempt_recipe
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.runtime.run_index import find_runs

pytestmark = [pytest.mark.git, pytest.mark.usefixtures("full_pipeline")]


def do_attempts(repo: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> tuple[int, Any]:
    triage_says(monkeypatch, intents=["feature"])
    monkeypatch.setattr(select_module, "build_judge_llm", lambda settings: None)  # the fallback
    use_runner(monkeypatch, FakeRunner(plan=PLAN, specs=[SPEC]))
    code = main.run(
        [
            "--workspace-root", str(workspace_root()), "do", REQUEST, "--yes", "--non-interactive",
            "--repo", str(repo), "--strategy", "pipeline", "--attempts", "2", *extra,
        ]
    )  # fmt: skip
    return code, find_runs(workspace_root())[-1]


@pytest.mark.parametrize("extra", [(), ("--worktree",)], ids=["branch", "worktree"])
def test_attempts_on_a_repository_pass_and_the_run_gets_the_chosen_ones_patch_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    base = git(repo, "rev-parse", "HEAD")

    code, ref = do_attempts(repo, monkeypatch, *extra)

    assert code == 0
    assert ref.manifest.status == "succeeded" and ref.manifest.verdict == "verified"
    found = AttemptsRecord.load(ref.run_dir)
    assert found is not None
    assert [(a.label, a.status) for a in found.attempts] == [("a1", "passed"), ("a2", "passed")]
    assert found.selection is not None and found.selection.revision_match is True
    # What a run that changes a project always leaves, now for the chosen attempt.
    assert_patch_applies(repo, base, ref.run_dir / PATCH_FILE, tmp_path)
    summary = (ref.run_dir / SUMMARY_FILE).read_text(encoding="utf-8")
    assert "# Change summary: Search notes" in summary and "`src/wp-1.py`" in summary
    assert "Verification: **verified**" in summary
    state = state_of(ref)
    iso = read_isolation(Path(ref.workspace))
    assert iso is not None and state.isolation is not None
    assert state.isolation["base_commit"] == base == iso.base_commit
    assert "summary" in state.summaries


def test_an_attempt_walks_the_recipe_without_the_stage_that_needs_the_runs_own_branch() -> None:
    recipe = load_recipe("feature")
    assert any(s.action == "change_summary" for s in recipe.stages)

    walked = attempt_recipe(recipe)

    assert [s.name for s in walked.stages] == [
        s.name for s in recipe.stages if s.action != "change_summary"
    ]
    assert attempt_recipe(walked) is walked  # nothing left to take out
    assert walked.name == recipe.name
