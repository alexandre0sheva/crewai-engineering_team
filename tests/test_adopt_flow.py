"""The ``adopt`` recipe end to end: isolate a repository, record its baseline, map its code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pipeline_fakes import FakeRunner
from repo_fixtures import PYTHON_APP, make_repo, snapshot

from engineering_team.modes.adopt import adopt_recipe
from engineering_team.modes.baseline_report import load_baseline
from engineering_team.modes.codebase_map import map_path
from engineering_team.modes.isolation import Isolation, isolate
from engineering_team.pipeline import strategies
from engineering_team.pipeline.runner import execute_run
from engineering_team.pipeline.state import PipelineState, RunBundle
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import load_settings
from engineering_team.tools.workspace import ProjectWorkspace

FAILING = {
    **PYTHON_APP,
    "tests/test_store.py": (
        "from app.store import add\n\n\ndef test_add():\n    assert add([], 'a') == ['a']\n\n\n"
        "def test_known_bug():\n    assert add(['a'], 'b') == ['b', 'a'], 'pre-existing failure'\n"
    ),
}


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", scripts + ":" + __import__("os").environ["PATH"])


def adopt(
    iso: Isolation, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner | None = None
) -> tuple[RunContext, Any]:
    """Run the whole ``adopt`` recipe in the workspace an isolation made."""

    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: runner or FakeRunner())
    settings = load_settings().with_overrides(
        {"project_name": "legacy", "strategy": "pipeline"}, source="test"
    )
    workspace = ProjectWorkspace.create(iso.workspace)
    ctx = RunContext.create(settings, workspace, run_id=new_run_id())
    recipe = adopt_recipe()
    RunRecorder.begin(ctx, mode="adopt", request="adopt", strategy="pipeline", recipe=recipe.name)
    result = execute_run(
        ctx, RunBundle(requirements="adopt"), strategy=strategies.get_strategy("pipeline"),
        recipe=recipe,
    )  # fmt: skip
    return ctx, result


def git_out(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_adopting_a_dirty_repository_leaves_it_alone_and_records_baseline_and_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", FAILING)
    (repo / "app" / "store.py").write_text("def add(items, text):\n    return items  # wip\n")
    (repo / "scratch.txt").write_text("untracked\n")
    before, head = snapshot(repo), git_out(repo, "rev-parse", "HEAD")
    iso = isolate(repo, run_id="20261003-101500-abc123", slug="adopt")

    ctx, result = adopt(iso, monkeypatch)

    assert result.status == "succeeded", result.error
    assert snapshot(repo) == before and git_out(repo, "rev-parse", "HEAD") == head  # untouched
    assert git_out(repo, "branch", "--show-current") == "main"
    manifest = RunStore(iso.workspace).load(ctx.run_id)
    assert [(s.name, s.status) for s in manifest.stages] == [
        ("profile", "succeeded"), ("baseline", "succeeded"), ("map", "succeeded"),
    ]  # fmt: skip
    state = PipelineState.load(ctx.run_dir)
    assert state is not None and state.profile is not None and state.baseline is not None
    assert state.baseline.known_failures == ["test:.:tests/test_store.py::test_known_bug"]
    assert load_baseline(iso.workspace) == state.baseline
    assert (iso.workspace / ".engineering-team" / "repo-profile.json").is_file()
    assert "## Architecture" in map_path(iso.workspace).read_text()
    assert state.profile.git.linked_worktree and not state.profile.git.dirty


@pytest.mark.git
def test_each_stage_is_committed_on_the_teams_branch_and_only_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", FAILING)
    head = git_out(repo, "rev-parse", "HEAD")
    iso = isolate(repo, run_id="20261003-101500-abc123", slug="adopt", mode="worktree")

    adopt(iso, monkeypatch)

    subjects = git_out(iso.workspace, "log", "--format=%s", f"{head}..HEAD").splitlines()
    assert [s.split(":")[0] for s in reversed(subjects)] == [
        "stage(profile)", "stage(baseline)", "stage(map)",
    ]  # fmt: skip
    assert git_out(repo, "rev-parse", "main") == head  # the user's branch did not move
    assert git_out(repo, "status", "--porcelain") == ""
    assert iso.branch is not None and git_out(repo, "rev-parse", iso.branch) != head


def test_a_copy_of_a_plain_directory_gets_the_same_treatment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "plain"
    for name, text in FAILING.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text(text)
    before = snapshot(source)
    iso = isolate(source, run_id="20261003-101500-abc123", slug="x", workspace_root=tmp_path / "ws")

    ctx, result = adopt(iso, monkeypatch)

    assert result.status == "succeeded", result.error
    assert snapshot(source) == before and not (source / ".engineering-team").exists()
    baseline = load_baseline(iso.workspace)
    assert baseline is not None and len(baseline.known_failures) == 1
    assert ctx.workspace.root == iso.workspace and map_path(iso.workspace).is_file()
