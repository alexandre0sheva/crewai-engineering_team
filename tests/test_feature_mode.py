"""``engineering-team feature``: a change to an existing project, from isolation to the patch."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner
from pipeline_fakes import FakeRunner
from repo_fixtures import PYTHON_APP, make_repo, snapshot, write_tree
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import AcceptanceCriterion, Plan, Spec, WorkPackage
from engineering_team.modes.change_report import PATCH_FILE, SUMMARY_FILE
from engineering_team.modes.isolation import read_isolation
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.run_index import find_runs
from engineering_team.runtime.run_store import RunStore

pytestmark = pytest.mark.git  # stage commits are part of what these tests are about
runner = CliRunner()
WORKSPACE_ROOT = "ws"
REQUEST = "Add a search function that finds notes by text."

# The project already had one failing test (the baseline) and one passing one.
LEGACY = {
    **PYTHON_APP,
    "tests/test_store.py": (
        "from app.store import add\n\n\ndef test_add():\n    assert add([], 'a') == ['a']\n\n\n"
        "def test_known_bug():\n    assert add(['a'], 'b') == ['b', 'a'], 'pre-existing failure'\n"
    ),
}
SPEC = Spec(
    title="Search notes",
    summary="Find notes by text.",
    criteria=[AcceptanceCriterion(id="AC-1", text="search returns matching notes")],
)
PLAN = Plan(
    stack="python",
    work_packages=[
        WorkPackage(
            id="WP-1",
            title="Search",
            role="backend",
            owned_paths=["src/wp-1.py", "app/store.py"],
            criteria_ids=["AC-1"],
        )
    ],
)


def feature_runner(
    on_call: Callable[[StageRequest], None] | None = None, **options: Any
) -> FakeRunner:
    return FakeRunner(plan=PLAN, specs=[SPEC], on_call=on_call, **options)


def run_feature(
    repo: Path, monkeypatch: pytest.MonkeyPatch, *extra: str, fake: FakeRunner | None = None
) -> tuple[Any, FakeRunner]:
    scripted = use_runner(monkeypatch, fake or feature_runner())
    result = runner.invoke(
        app,
        [
            "--workspace-root",
            str(Path.cwd() / WORKSPACE_ROOT),
            "feature",
            "--repo",
            str(repo),
            "--request",
            REQUEST,
            *extra,
        ],  # fmt: skip
    )
    return result, scripted


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def latest(repo: Path | None = None) -> Any:
    refs = find_runs(Path.cwd() / WORKSPACE_ROOT)
    assert refs, "no run was recorded"
    return refs[-1]


def state_of(ref: Any) -> PipelineState:
    state = PipelineState.load(ref.run_dir)
    assert state is not None
    return state


def assert_patch_applies(repo: Path, base: str, patch: Path, tmp: Path) -> None:
    """The patch applies cleanly to the commit the team started from, in a fresh clone."""

    clone = tmp / "fresh-clone"
    git(tmp, "clone", "-q", "--no-hardlinks", str(repo), str(clone))
    git(clone, "checkout", "-q", base)
    git(clone, "apply", "--check", str(patch))
    git(clone, "apply", str(patch))
    assert (clone / "src" / "wp-1.py").is_file()


# -- the whole run -------------------------------------------------------------------------------


def test_a_feature_run_ends_on_a_new_branch_verified_against_its_baseline_with_an_applicable_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    base = git(repo, "rev-parse", "HEAD")

    result, fake = run_feature(repo, monkeypatch)

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.mode == "feature" and ref.manifest.recipe == "feature"
    assert ref.manifest.status == "succeeded" and ref.manifest.verdict == "verified"
    iso = read_isolation(repo)
    assert iso is not None and iso.mode == "branch" and iso.base_commit == base
    assert git(repo, "branch", "--show-current") == iso.branch
    assert iso.branch and iso.branch.startswith("engineering-team/")
    assert [s.name for s in ref.manifest.stages] == [
        "profile", "baseline", "map", "spec", "impact", "implement", "tests", "verify", "review",
        "summary",
    ]  # fmt: skip
    assert all(s.status == "succeeded" for s in ref.manifest.stages)
    subjects = git(repo, "log", "--format=%s", f"{base}..HEAD").splitlines()
    assert len(subjects) == 10 and subjects[0].startswith("stage(summary)")
    assert git(repo, "status", "--porcelain") == ""
    # What the project did not have before: the controller's write-ups stay out of the diff.
    assert not (repo / "docs").exists()
    for name in ("verification.md", "spec.md", "review.md"):
        assert (ref.run_dir / "reports" / name).is_file(), name
    assert (
        "known failure(s) from the baseline, no new ones"
        in (ref.run_dir / "reports" / "verification.md").read_text()
    )
    state = state_of(ref)
    assert state.baseline is not None
    assert state.baseline.known_failures == ["test:.:tests/test_store.py::test_known_bug"]
    tests_check = next(c for c in state.checks if c.id == "tests")
    assert (
        tests_check.status == "passed"
        and tests_check.known_failures == state.baseline.known_failures
    )
    assert tests_check.new_failures == []
    patch = ref.run_dir / PATCH_FILE
    assert_patch_applies(repo, base, patch, tmp_path)
    summary = (ref.run_dir / SUMMARY_FILE).read_text()
    assert "# Change summary: Search notes" in summary and "`src/wp-1.py`" in summary
    assert "Verification: **verified**" in summary and "Nothing was pushed" in summary
    assert any(r.stage.name == "implement" and r.package for r in fake.requests)


def test_the_codebase_map_reaches_the_agents_that_plan_and_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)

    _, fake = run_feature(repo, monkeypatch)

    names = [(r.stage.name, r.teammate) for r in fake.requests]
    assert ("impact", "solution_architect") in names and ("tests", "quality_engineer") in names
    assert ("spec", "product_analyst") in names
    analysts = [r for r in fake.requests if r.stage.kind == "analyze"]
    assert analysts and all(r.teammate == "codebase_analyst" for r in analysts)
    assert (repo / ".engineering-team" / "codebase-map.md").is_file()


def test_a_regression_the_baseline_does_not_excuse_fails_the_run_with_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)

    def break_add(request: StageRequest) -> None:
        if request.package is not None:  # the change breaks a test that passed before
            request.ctx.workspace.write_file(
                "app/store.py", "def add(items, text):\n    return []\n"
            )

    fake = feature_runner(break_add)

    result, _ = run_feature(repo, monkeypatch, fake=fake)

    assert result.exit_code == 3, result.output
    ref = latest()
    assert ref.manifest.verdict == "failed"
    state = state_of(ref)
    check = next(c for c in state.checks if c.id == "tests")
    assert check.status == "failed" and check.new_failures == [
        "test:.:tests/test_store.py::test_add"
    ]
    assert check.known_failures == ["test:.:tests/test_store.py::test_known_bug"]
    repairs = [r for r in fake.requests if r.failures]
    assert repairs
    brief = repairs[0].failures
    assert "new failures (fix these): test:.:tests/test_store.py::test_add" in brief
    assert "not yours to fix" in brief and "test_known_bug" in brief
    report = (ref.run_dir / "reports" / "verification.md").read_text()
    assert "- NEW: test:.:tests/test_store.py::test_add" in report
    assert "- known: test:.:tests/test_store.py::test_known_bug" in report
    assert not (ref.run_dir / SUMMARY_FILE).exists()  # the run stopped at verify


def test_a_repair_that_removes_the_new_failure_makes_it_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)

    def work(request: StageRequest) -> None:
        if request.package is not None:
            request.ctx.workspace.write_file(
                "app/store.py", "def add(items, text):\n    return []\n"
            )
        elif request.failures:  # the debugger restores the behaviour
            request.ctx.workspace.write_file(
                "app/store.py", "def add(items, text):\n    return [*items, text]\n"
            )

    result, _ = run_feature(repo, monkeypatch, fake=feature_runner(work))

    assert result.exit_code == 0, result.output
    assert latest().manifest.verdict == "verified"
    assert state_of(latest()).verification.rounds == 1


def test_a_file_outside_the_plans_scope_is_flagged_as_diff_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)

    def noisy(request: StageRequest) -> None:
        if request.package is not None:
            request.ctx.workspace.write_file("scripts/cleanup.py", "x = 1\n" * 12)
        if request.stage.name == "tests":
            request.ctx.workspace.write_file("tests/test_search.py", "def test_s():\n    pass\n")

    result, _ = run_feature(repo, monkeypatch, fake=feature_runner(noisy))

    assert result.exit_code == 0, result.output
    ref = latest()
    noise = state_of(ref).diff_noise
    assert noise is not None and noise.outside_files == 1
    assert [(n.path, n.lines) for n in noise.outside] == [("scripts/cleanup.py", 12)]
    assert noise.outside_lines == 12 and noise.total_lines > 12
    review = (ref.run_dir / "reports" / "review.md").read_text()
    assert "## Diff noise" in review and "`scripts/cleanup.py`: 12 line(s)" in review
    assert "`scripts/cleanup.py`" in (ref.run_dir / SUMMARY_FILE).read_text()  # and in the summary
    events = (ref.run_dir / "events.jsonl").read_text()
    assert "review.diff_noise" in events and "change.summary" in events


# -- where the team works -----------------------------------------------------------------------


def test_a_dirty_repository_gets_a_worktree_and_stays_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    (repo / "app" / "store.py").write_text("def add(items, text):\n    return items  # wip\n")
    (repo / "notes.txt").write_text("untracked\n")
    before, head = snapshot(repo), git(repo, "rev-parse", "HEAD")

    result, _ = run_feature(repo, monkeypatch)

    assert result.exit_code == 0, result.output
    assert snapshot(repo) == before and git(repo, "rev-parse", "main") == head
    assert git(repo, "branch", "--show-current") == "main"
    ref = latest()
    assert ref.workspace.parent == (repo / ".engineering-team" / "worktrees").resolve()
    assert (ref.workspace / "src" / "wp-1.py").is_file() and not (repo / "src").exists()
    assert "separate working copy" in result.stderr or "worktree" in result.stderr


def test_a_directory_without_git_is_copied_and_made_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_tree(tmp_path / "plain", LEGACY)
    before = snapshot(source)

    result, _ = run_feature(source, monkeypatch)

    assert result.exit_code == 0, result.output
    assert snapshot(source) == before and not (source / ".git").exists()
    ref = latest()
    assert ref.workspace == (Path.cwd() / WORKSPACE_ROOT / "plain").resolve()
    iso = read_isolation(ref.workspace)
    assert iso is not None and iso.mode == "copy" and iso.base_commit
    assert git(ref.workspace, "rev-list", "--count", f"{iso.base_commit}..HEAD") != "0"
    assert (ref.run_dir / PATCH_FILE).is_file()


def test_squash_leaves_one_commit_on_the_team_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    base = git(repo, "rev-parse", "HEAD")

    result, _ = run_feature(repo, monkeypatch, "--squash")

    assert result.exit_code == 0, result.output
    assert git(repo, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert git(repo, "log", "-1", "--format=%s") == "feature: Search notes"
    assert git(repo, "show", "--stat", "--format=", "HEAD").count("wp-1.py") == 1
    assert git(repo, "status", "--porcelain") == ""


def test_worktree_can_be_forced_on_a_clean_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    result, _ = run_feature(repo, monkeypatch, "--worktree")
    assert result.exit_code == 0, result.output
    assert latest().workspace != repo.resolve() and git(repo, "branch", "--show-current") == "main"


# -- looking at it and taking it -----------------------------------------------------------------


def test_diff_and_export_patch_show_and_carry_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    base = git(repo, "rev-parse", "HEAD")
    run_feature(repo, monkeypatch)
    ref = latest()
    common = ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT)]

    shown = runner.invoke(app, [*common, "diff", ref.run_id[:14]])
    stat = runner.invoke(app, [*common, "diff", "--stat"])
    out = tmp_path / "out" / "change.patch"
    out.parent.mkdir()
    exported = runner.invoke(app, [*common, "export-patch", ref.run_id, "--out", str(out)])
    again = runner.invoke(app, [*common, "export-patch", "--out", str(out)])
    inside = runner.invoke(app, [*common, "export-patch", "--out", str(repo / "x.patch")])
    as_json = runner.invoke(app, ["--json", *common, "diff"])

    assert shown.exit_code == 0 and "+++ b/src/wp-1.py" in shown.stdout
    assert "src/wp-1.py" in stat.stdout and "+++" not in stat.stdout
    assert exported.exit_code == 0 and out.is_file() and "git apply" in exported.stdout
    assert_patch_applies(repo, base, out, tmp_path)
    assert again.exit_code == 2 and "already exists" in again.stderr
    assert inside.exit_code == 2 and "outside the project" in inside.stderr
    assert json.loads(as_json.stdout)["base"] == base


def test_runs_status_and_board_find_a_run_that_lives_in_your_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    run_feature(repo, monkeypatch)
    ref = latest()
    common = ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT)]

    listing = runner.invoke(app, [*common, "runs"])
    status = runner.invoke(app, ["--json", *common, "status", ref.run_id])

    assert ref.run_id in listing.stdout and "legacy" in listing.stdout
    data = json.loads(status.stdout)
    assert data["mode"] == "feature" and data["verdict"] == "verified"
    assert data["workspace"] == str(repo.resolve())
    assert RunStore(repo.resolve()).latest() is not None


def test_a_failed_feature_run_can_be_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    first, _ = run_feature(repo, monkeypatch, fake=feature_runner(fail={"implement:WP-1": 9}))
    assert first.exit_code == 1
    ref = latest()
    assert ref.manifest.status == "failed"
    healthy = use_runner(monkeypatch, feature_runner())

    result = runner.invoke(
        app,
        ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT), "resume", ref.run_id],
    )

    assert result.exit_code == 0, result.output
    again = RunStore(repo.resolve()).load(ref.run_id)
    assert again.status == "succeeded" and again.verdict == "verified" and again.resumes == 1
    assert [r.stage.name for r in healthy.requests if r.stage.kind != "analyze"][:1] == [
        "implement"
    ]
    assert (ref.run_dir / PATCH_FILE).is_file()
    changed = runner.invoke(
        app,
        [
            "--workspace-root",
            str(Path.cwd() / WORKSPACE_ROOT),
            "resume",
            ref.run_id,
            "--request",
            "something else",
        ],  # fmt: skip
    )
    assert changed.exit_code == 2


# -- refusals ----------------------------------------------------------------------------------


def test_usage_errors_exit_2_and_touch_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    before = snapshot(repo)
    use_runner(monkeypatch)

    missing = runner.invoke(app, ["feature", "--repo", str(tmp_path / "nowhere"), "--request", "x"])
    no_request = runner.invoke(app, ["feature", "--repo", str(repo)])
    sub = make_repo(tmp_path / "mono", {f"svc/{k}": v for k, v in PYTHON_APP.items()})
    inside = runner.invoke(app, ["feature", "--repo", str(sub / "svc"), "--request", REQUEST])

    assert missing.exit_code == 2 and "is not a directory" in missing.stderr
    assert no_request.exit_code == 2 and "No project request found" in no_request.stderr
    assert inside.exit_code == 2 and "inside the Git repository" in inside.stderr
    assert snapshot(repo) == before and not (repo / ".engineering-team").exists()
    assert git(repo, "branch", "--list", "engineering-team/*") == ""
