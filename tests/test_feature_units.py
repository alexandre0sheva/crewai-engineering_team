"""The parts of feature mode, one at a time: baseline comparison, diff noise, Git changes and
squash, report locations, recipe options, and finding runs outside the workspace root."""

from __future__ import annotations

from pathlib import Path

import pytest
from git_helpers import git as run_git
from repo_fixtures import make_repo

from engineering_team.contracts import CheckResult, Plan, WorkPackage
from engineering_team.git.port import Change, GitError
from engineering_team.modes.baseline_report import BaselineReport
from engineering_team.modes.diff_noise import measure, touched_scope
from engineering_team.modes.repo_analyzer import standalone_git
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.pipeline.packages import plan_problems
from engineering_team.pipeline.recipes import RecipeError, StageSpec, load_recipe, parse_recipe
from engineering_team.runtime.reports import QA_NOTES, VERIFICATION, Reports
from engineering_team.runtime.run_index import find_runs, register_workspace
from engineering_team.runtime.run_store import RunStore
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.verification.baseline_compare import apply_baseline
from engineering_team.verification.verdict import failures_for_repair, judge

BASELINE = BaselineReport(
    known_failures=["test:.:t::old", "lint:.:a.py:F401", "build:."], revision="r"
)


def failed(kind: str, report: dict | None = None, **fields: object) -> CheckResult:
    return CheckResult(
        id=kind,
        status="failed",
        kind=kind,
        report=report,
        revision="r",
        summary="boom",
        **fields,  # type: ignore[arg-type]
    )


def failures_of(*ids: str) -> dict:
    return {"failures": [{"test_id": i} for i in ids]}


# -- comparing with the baseline -----------------------------------------------------------------


def test_a_check_with_only_known_failures_counts_as_passed_and_says_so() -> None:
    (result,) = apply_baseline([failed("test", failures_of("t::old"))], {"test": "."}, BASELINE)

    assert result.status == "passed" and result.known_failures == ["test:.:t::old"]
    assert result.new_failures == [] and "1 known failure(s) from the baseline" in result.summary
    assert judge([result], "r").verdict == "verified"


def test_a_new_failure_beside_a_known_one_stays_a_failure_and_names_both() -> None:
    (result,) = apply_baseline(
        [failed("test", failures_of("t::old", "t::fresh"))], {"test": "."}, BASELINE
    )

    assert result.status == "failed"
    assert result.new_failures == ["test:.:t::fresh"] and result.known_failures == ["test:.:t::old"]
    brief = failures_for_repair([result])
    assert "new failures (fix these): test:.:t::fresh" in brief
    assert "not yours to fix (leave them): test:.:t::old" in brief
    assert judge([result], "r").verdict == "failed"


def test_a_failure_the_baseline_never_had_is_new() -> None:
    (result,) = apply_baseline([failed("test", failures_of("t::fresh"))], {"test": "."}, BASELINE)

    assert result.status == "failed" and result.new_failures == ["test:.:t::fresh"]
    assert result.known_failures == []


def test_lint_keys_ignore_the_line_and_a_whole_check_key_matches_a_whole_check() -> None:
    lint = failed(
        "lint",
        {"diagnostics": [{"file": "a.py", "line": 99, "rule": "F401", "severity": "error"}]},
    )
    build = failed("build")

    lint_result, build_result = apply_baseline([lint, build], {"lint": ".", "build": "."}, BASELINE)

    assert lint_result.status == "passed" and build_result.status == "passed"


def test_directories_are_part_of_the_key_and_other_checks_are_left_alone() -> None:
    web = failed("test", failures_of("t::old"))
    passing = CheckResult(id="p", status="passed", kind="test", revision="r")
    custom = CheckResult(id="c", status="failed", kind="custom", revision="r")
    user = failed("test", failures_of("t::old")).model_copy(update={"source": "user"})

    results = apply_baseline([web, passing, custom, user], {"test": "web"}, BASELINE)

    assert results[0].status == "failed" and results[0].new_failures == ["test:web:t::old"]
    assert results[1:3] == [passing, custom]
    assert results[3].status == "failed" and results[3].known_failures == []  # user checks: raw


# -- diff noise ----------------------------------------------------------------------------------


def plan(*owned: str) -> Plan:
    return Plan(
        work_packages=[WorkPackage(id="WP-1", title="t", role="backend", owned_paths=list(owned))]
    )


def test_noise_is_the_lines_outside_the_plan_and_the_tests() -> None:
    profile = RepoProfile(name="p", root="/p", test_dirs=["tests", "web/spec"])
    scope = touched_scope(plan("app/search.py", "docs/"), profile)

    noise = measure(
        [
            ("app/search.py", 40),
            ("docs/usage.md", 5),
            ("tests/test_search.py", 30),
            ("web/spec/a.js", 4),
            ("app/other_test.py", 6),  # a test file by name, anywhere
            ("README.md", 3),
            ("scripts/x.py", 9),
        ],
        scope,
    )

    assert noise.total_lines == 97 and noise.files == 7
    assert [(n.path, n.lines) for n in noise.outside] == [("scripts/x.py", 9), ("README.md", 3)]
    assert noise.outside_lines == 12 and noise.outside_files == 2 and noise.ratio == 0.124


def test_noise_of_nothing_is_zero_and_the_list_is_capped() -> None:
    assert measure([], []).ratio == 0.0
    many = measure([(f"x/{n}.py", 1) for n in range(60)], ["app/"])
    assert many.outside_files == 60 and len(many.outside) == 25


# -- Git: changes and squash ---------------------------------------------------------------------


def test_changes_lists_files_with_status_and_lines_including_new_and_binary(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "r")
    base = run_git_out(repo, "rev-parse", "HEAD")
    (repo / "app" / "store.py").write_text("def add(items, text):\n    return items\n")
    (repo / "new.py").write_text("a = 1\nb = 2\n")
    (repo / "logo.bin").write_bytes(b"\x00\x01\x02")
    (repo / "README.md").unlink()

    with standalone_git(repo) as port:
        found = port.changes(base)

    by_path = {c.path: c for c in found}
    assert by_path["new.py"] == Change("A", "new.py", 2, 0)
    assert by_path["README.md"].status == "D" and by_path["README.md"].removed == 3
    assert by_path["app/store.py"].status == "M" and by_path["app/store.py"].lines == 2
    assert by_path["logo.bin"].added is None and by_path["logo.bin"].lines == 1
    assert [c.path for c in found] == sorted(by_path)
    assert not (repo / ".engineering-team" / "tmp" / "git" / "index-1").exists()  # cleaned up


def run_git_out(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_squash_makes_everything_since_the_base_one_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    base = run_git_out(repo, "rev-parse", "HEAD")
    with standalone_git(repo) as port:
        (repo / "a.py").write_text("a = 1\n")
        port.checkpoint("stage(one)")
        (repo / "b.py").write_text("b = 1\n")
        port.checkpoint("stage(two)")
        (repo / "c.py").write_text("uncommitted = 1\n")  # included, like the final commit

        sha = port.squash(base, "feature: add things")

    assert run_git_out(repo, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert run_git_out(repo, "rev-parse", "HEAD") == sha
    assert run_git_out(repo, "log", "-1", "--format=%s") == "feature: add things"
    assert sorted(run_git_out(repo, "show", "--name-only", "--format=", "HEAD").split()) == [
        "a.py", "b.py", "c.py",
    ]  # fmt: skip
    assert run_git_out(repo, "status", "--porcelain") == ""


def test_squash_refuses_a_base_that_is_not_behind_head(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    run_git(repo, "checkout", "-q", "-b", "other")
    (repo / "x.py").write_text("x = 1\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "other work")
    other = run_git_out(repo, "rev-parse", "HEAD")
    run_git(repo, "checkout", "-q", "main")

    with standalone_git(repo) as port, pytest.raises(GitError, match="not an ancestor"):
        port.squash(other, "nope")

    assert run_git_out(repo, "rev-parse", "main") != other


# -- where the controller's reports go -----------------------------------------------------------


def test_reports_live_in_docs_for_a_new_project_and_in_the_run_directory_otherwise(
    tmp_path: Path,
) -> None:
    ws = ProjectWorkspace.create(tmp_path / "p")
    project = Reports(ws)
    elsewhere = Reports.in_run_dir(ws, tmp_path / "run")

    project.write(VERIFICATION, "# v\n")
    elsewhere.write(VERIFICATION, "# v\n")

    assert (tmp_path / "p" / "docs" / "verification.md").read_text() == "# v\n"
    assert (tmp_path / "run" / "reports" / "verification.md").read_text() == "# v\n"
    assert not (tmp_path / "p" / "reports").exists()
    assert project.label(QA_NOTES) == "docs/qa-notes.md" and project.in_project
    assert "run directory" in elsewhere.label(QA_NOTES) and not elsewhere.in_project
    with pytest.raises(FileNotFoundError):
        elsewhere.read("review.md")


# -- recipe options and plan validation ----------------------------------------------------------


def test_the_feature_recipe_is_the_plans_pipeline() -> None:
    recipe = load_recipe("feature")

    assert [(s.name, s.kind) for s in recipe.stages] == [
        ("profile", "controller"), ("baseline", "controller"), ("map", "analyze"),
        ("spec", "agent"), ("impact", "agent"), ("implement", "parallel"), ("tests", "agent"),
        ("verify", "verify"), ("review", "review"), ("summary", "controller"),
    ]  # fmt: skip
    assert recipe.stage("implement").allow_shared and recipe.stage("implement").prompt
    assert recipe.stage("impact").outputs == ["plan"] and recipe.stage("summary").action
    assert all(not s.file_outputs for s in recipe.stages)  # nothing promised inside the project


def test_allow_shared_is_for_parallel_stages_only() -> None:
    with pytest.raises(ValueError, match="only parallel stages take allow_shared"):
        StageSpec(name="x", kind="agent", teammates=["a"], allow_shared=True)
    text = "name: x\nstages:\n  - {name: a, kind: agent, teammates: [q], allow_shared: true}\n"
    with pytest.raises(RecipeError, match="allow_shared"):
        parse_recipe(text, source="x.yaml")


def test_packages_may_own_shared_files_only_when_the_stage_allows_it() -> None:
    owning = plan("package.json", "src/")

    assert any("shared file(s) package.json" in p for p in plan_problems(owning))
    assert plan_problems(owning, allow_shared=True) == []


# -- finding runs that are not under the workspace root -----------------------------------------


def test_registered_workspaces_are_found_with_their_runs(tmp_path: Path) -> None:
    from engineering_team.contracts import RunManifest

    root = tmp_path / "ws"
    external = tmp_path / "somewhere" / "worktree"
    RunStore(external).create(RunManifest(run_id="20261003-101500-abc123", project_name="legacy"))
    register_workspace(root, external, "legacy")
    register_workspace(root, external, "legacy")  # again: no duplicate
    register_workspace(root, tmp_path / "gone", "ghost")  # a workspace with no runs is skipped

    refs = find_runs(root)

    assert [(r.project, r.workspace, r.run_id) for r in refs] == [
        ("legacy", external.resolve(), "20261003-101500-abc123")
    ]
    assert find_runs(root, "legacy") == refs and find_runs(root, "other") == []
