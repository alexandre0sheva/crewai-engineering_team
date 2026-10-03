"""Git in a pipeline run: a repository for a new project and one commit per finished stage."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from git_helpers import require_git
from pipeline_fakes import FakeRunner
from test_pipeline_flow import only_run, project, resume, run_dir, start, use_runner

from engineering_team.git.port import DEFAULT_EMAIL, DEFAULT_NAME, GitError, GitPort
from engineering_team.pipeline.stages import StageRequest
from engineering_team.runtime.events import read_events

STAGES = ["spec", "plan", "foundation", "implement", "integrate", "verify", "release"]


pytestmark = pytest.mark.git


@pytest.fixture(autouse=True)
def needs_git() -> None:
    require_git()


def git(*args: str) -> str:
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null"}
    done = subprocess.run(
        ["git", *args], cwd=project(), env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def subjects() -> list[str]:
    """Commit subjects, oldest first."""

    return git("log", "--reverse", "--format=%s").splitlines()


def test_a_greenfield_run_leaves_a_repository_with_one_commit_per_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    messages = subjects()
    assert messages[0] == "Initial commit"
    assert [m.split(":")[0] for m in messages[1:]] == [f"stage({name})" for name in STAGES]
    assert messages[1] == "stage(spec): spec done"
    assert git("log", "-1", "--format=%an <%ae>") == f"{DEFAULT_NAME} <{DEFAULT_EMAIL}>"
    # Everything is committed, and none of the controller's own state is.
    assert git("status", "--porcelain") == ""
    assert not [f for f in git("ls-files").splitlines() if f.startswith(".engineering-team")]
    assert git("show", "--name-only", "--format=", "HEAD~6") == ""  # stage(spec): wrote nothing
    assert "docs/architecture.md" in git("show", "--name-only", "--format=", "HEAD~5")
    assert "docs/verification.md" in git("show", "--name-only", "--format=", "HEAD~1")
    events = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert events.count("git.checkpoint") == len(STAGES) and events.count("git.init") == 1


def test_no_git_leaves_the_project_without_a_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())

    assert start("--no-git") == 0

    assert not (project() / ".git").exists()


def test_a_skipped_stage_gets_no_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team.contracts import Plan

    use_runner(monkeypatch, FakeRunner(plan=Plan(stack="shell")))  # no work packages

    assert start() == 0

    names = [m.split(":")[0] for m in subjects()[1:]]
    assert "stage(implement)" not in names and "stage(integrate)" not in names
    assert names == [
        "stage(spec)",
        "stage(plan)",
        "stage(foundation)",
        "stage(verify)",
        "stage(release)",
    ]


def test_a_resumed_run_commits_only_the_stages_it_actually_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"integrate": 2}))
    assert start() == 1
    assert [m.split(":")[0] for m in subjects()[1:]] == [
        "stage(spec)", "stage(plan)", "stage(foundation)", "stage(implement)",
    ]  # fmt: skip

    use_runner(monkeypatch, FakeRunner())
    assert resume(only_run().run_id) == 0

    assert [m.split(":")[0] for m in subjects()[1:]] == [f"stage({name})" for name in STAGES]
    assert subjects().count("Initial commit") == 1


def test_an_existing_repository_is_continued_not_reinitialised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())
    assert start() == 0
    first = len(subjects())

    assert start() == 0  # a new run in the same project

    assert subjects().count("Initial commit") == 1
    assert len(subjects()) == first + len(STAGES)


def test_an_edit_the_final_check_undoes_is_committed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def forge(request: StageRequest) -> None:
        if request.stage.name == "release":
            request.ctx.workspace.write_file("docs/verification.md", "# Verification\nPASSED\n")

    use_runner(monkeypatch, FakeRunner(on_call=forge))

    assert start() == 0

    assert subjects()[-1].startswith("final:")
    assert git("status", "--porcelain") == ""
    assert "**Verdict: VERIFIED**" in git("show", "HEAD:docs/verification.md")


def test_a_git_failure_never_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: GitPort, message: str) -> str:
        raise GitError("index.lock exists")

    monkeypatch.setattr(GitPort, "checkpoint", broken)
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    warnings = [
        e for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == "git.warning"
    ]
    assert warnings and "index.lock exists" in warnings[0].data["error"]


def test_git_not_being_installed_only_costs_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitPort, "available", lambda self: False)
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    assert not (project() / ".git").exists()
    events = read_events(run_dir(only_run()) / "events.jsonl")
    assert any(e.type == "git.warning" and "not installed" in e.data["error"] for e in events)


def test_a_project_that_is_not_empty_is_not_turned_into_a_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from engineering_team.pipeline.checkpoints import Checkpoints

    # An existing project with files and no repository: init is for new projects only.
    root = project()
    root.mkdir(parents=True)
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert Checkpoints.is_new_project(root) is False
    assert Checkpoints.is_new_project(tmp_path / "empty-dir-that-does-not-exist") is True
