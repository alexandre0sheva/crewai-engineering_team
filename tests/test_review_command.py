"""``engineering-team review``: read-only, in place, findings for people and for CI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from maintain_helpers import git, latest, project, run_cli, state_of
from pipeline_fakes import FakeRunner
from test_baseline import interpreter_on_path  # noqa: F401  (the profile looks for tools)

from engineering_team.contracts import Finding, ReviewReport
from engineering_team.pipeline.stages import CrewStageRunner

pytestmark = pytest.mark.git
HIGH = Finding(
    severity="high", summary="add forgets the earlier notes", file="app/store.py", line=2
)
LOW = Finding(severity="low", summary="a docstring would help", file="app/store.py")


def topic_branch(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose ``topic`` branch changes the store one commit after ``main``."""

    repo = project(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "topic")
    (repo / "app" / "store.py").write_text("def add(items, text):\n    return [text]\n")
    git(repo, "commit", "-qam", "change the store")
    return repo, base


def review(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *extra: str,
    fake: FakeRunner,
    before: tuple[str, ...] = (),
):
    return run_cli(monkeypatch, "review", "--repo", str(repo), *extra, fake=fake, before=before)


def test_a_serious_finding_fails_the_review_with_exit_3_and_writes_findings_for_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = topic_branch(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    out = tmp_path / "artifacts"
    fake = FakeRunner(reviews={"code_reviewer": ReviewReport(summary="Broken.", findings=[HIGH])})

    result, fake = review(monkeypatch, repo, "--base", "main", "--out-dir", str(out), fake=fake)

    assert result.exit_code == 3, result.output
    ref = latest()
    assert ref.manifest.mode == "review" and ref.manifest.verdict == "failed"
    data = json.loads((ref.run_dir / "findings.json").read_text())
    assert data["kind"] == "review" and data["passed"] is False and data["fail_on"] == "high"
    assert data["base"] == base and data["base_branch"] == "main" and data["head"] == head
    assert data["counts"]["high"] == 1 and [f["id"] for f in data["findings"]] == ["F-1"]
    assert data["findings"][0]["source_role"] == "code_reviewer"
    assert "**Result: FAILED.**" in (ref.run_dir / "findings.md").read_text()
    assert (
        json.loads((out / "findings.json").read_text()) == data and (out / "findings.md").is_file()
    )
    reviewers = [r for r in fake.requests if r.stage.name == "review"]
    assert {r.teammate for r in reviewers} == {"code_reviewer", "security_engineer"}
    assert all(r.stage.prompt == "review_diff" for r in reviewers)
    assert all(r.state.isolation and r.state.isolation["base_commit"] == base for r in reviewers)
    assert CrewStageRunner._inputs(reviewers[0])["base"] == base[:12]
    assert "needs" not in result.stderr


def test_a_review_changes_nothing_in_your_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = topic_branch(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    result, _ = review(monkeypatch, repo, "--base", "main", fake=FakeRunner())

    assert result.exit_code == 0, result.output
    assert (
        git(repo, "rev-parse", "HEAD") == head and git(repo, "branch", "--show-current") == "topic"
    )
    assert git(repo, "status", "--porcelain") == ""  # the controller's state is ignored by Git
    assert git(repo, "branch", "--list", "engineering-team/*") == ""
    assert not (repo / "docs").exists()  # reports went to the run directory
    assert (latest().run_dir / "findings.json").is_file()


def test_findings_below_the_threshold_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = topic_branch(tmp_path)
    fake = FakeRunner(reviews={"code_reviewer": ReviewReport(findings=[LOW])})

    result, _ = review(monkeypatch, repo, "--base", "main", fake=fake, before=("--json",))

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["verdict"] == "verified" and data["passed"] is True
    assert data["findings"][0]["severity"] == "low" and data["counts"]["low"] == 1
    assert data["findings_json"].endswith("findings.json")
    assert not any("resume" in step for step in data["next_steps"])


def test_the_threshold_is_review_fail_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = topic_branch(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text('[review]\nfail_on = "critical"\n')
    fake = FakeRunner(reviews={"code_reviewer": ReviewReport(findings=[HIGH])})

    result, _ = review(monkeypatch, repo, "--base", "main", "--config", str(config), fake=fake)

    assert result.exit_code == 0, result.output
    assert json.loads((latest().run_dir / "findings.json").read_text())["fail_on"] == "critical"


def test_without_a_base_the_uncommitted_changes_are_reviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    (repo / "app" / "store.py").write_text("def add(items, text):\n    return []\n")

    result, fake = review(monkeypatch, repo, fake=FakeRunner())

    assert result.exit_code == 0, result.output
    assert state_of(latest()).isolation["base_commit"] == head  # type: ignore[index]
    assert any(r.stage.name == "review" for r in fake.requests)


def test_a_clean_default_branch_has_nothing_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    result, fake = review(monkeypatch, repo, fake=FakeRunner())

    assert result.exit_code == 0, result.output
    assert not any(r.stage.name == "review" for r in fake.requests)
    ref = latest()
    assert "Nothing to review" in state_of(ref).summaries["target"]
    assert json.loads((ref.run_dir / "findings.json").read_text())["findings"] == []


def test_a_branch_with_no_base_is_compared_with_the_default_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = topic_branch(tmp_path)

    result, _ = review(monkeypatch, repo, fake=FakeRunner())

    assert result.exit_code == 0, result.output
    assert state_of(latest()).isolation["base_commit"] == base  # type: ignore[index]


def test_a_review_that_cannot_start_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = topic_branch(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()

    unknown, _ = review(monkeypatch, repo, "--base", "no-such-branch", fake=FakeRunner())
    no_repo, _ = review(monkeypatch, plain, fake=FakeRunner())
    missing, _ = review(monkeypatch, tmp_path / "nowhere", fake=FakeRunner())

    assert unknown.exit_code == 2 and "'no-such-branch' is not a branch" in unknown.stderr
    assert no_repo.exit_code == 2 and "needs one" in no_repo.stderr
    assert missing.exit_code == 2 and "is not a directory" in missing.stderr
