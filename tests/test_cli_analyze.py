"""``engineering-team analyze``: the profile, the deep map, and that the project is left alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_helpers import use_runner
from pipeline_fakes import FakeRunner
from repo_fixtures import NODE_APP, PYTHON_APP, make_repo, snapshot, write_tree
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.modes.codebase_map import map_path
from engineering_team.runtime.run_store import RunStore

runner = CliRunner()


def analyze(*args: str, json_output: bool = False):  # noqa: ANN201
    prefix = ["--json"] if json_output else []
    return runner.invoke(app, [*prefix, "analyze", *args])


def test_analyze_prints_the_profile_and_changes_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "notes")
    before = snapshot(root)
    listing = sorted(p.name for p in root.iterdir())

    result = analyze("--repo", str(root))

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Project: notes" in out and "Python" in out
    assert "test      make test  (Makefile target 'test')" in out
    assert "Git: Git repository (branch main" in out and "clean" in out
    assert "Entry points:" in out and "CI: .github/workflows/ci.yml" in out
    assert "Codebase map: none yet. Run with --deep" in out
    assert snapshot(root) == before and sorted(p.name for p in root.iterdir()) == listing


def test_analyze_defaults_to_the_current_directory(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    root = write_tree(tmp_path / "shop", NODE_APP)
    monkeypatch.chdir(root)

    result = analyze()

    assert result.exit_code == 0 and "Project: shop" in result.stdout
    assert "npm test" in result.stdout and "npm ci" in result.stdout


def test_analyze_json_is_one_document_with_the_profile(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "shop", NODE_APP)

    result = analyze("--repo", str(root), json_output=True)

    data = json.loads(result.stdout)
    assert result.exit_code == 0 and data["deep"] is False
    assert data["profile"]["name"] == "shop"
    assert data["profile"]["stacks"][0]["language"] == "javascript"
    assert {"kind": "test", "command": "npm test"}.items() <= {
        k: v for k, v in data["profile"]["commands"][1].items() if k in ("kind", "command")
    }.items()
    assert data["map"] == {"path": None, "current": None}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--repo", "nowhere"], "is not a directory"),
        (["--refresh"], "--refresh needs --deep"),
    ],
)
def test_analyze_usage_errors_exit_2(tmp_path: Path, args: list[str], message: str) -> None:
    result = analyze(*args)

    assert result.exit_code == 2 and message in result.stderr


# -- --deep ------------------------------------------------------------------------------------


def deep(root: Path, *extra: str, monkeypatch: pytest.MonkeyPatch, fake: FakeRunner | None = None):  # noqa: ANN201
    runner_ = use_runner(monkeypatch, fake)
    return analyze("--repo", str(root), "--deep", *extra), runner_


def test_deep_writes_the_map_beside_the_profile_and_leaves_the_project_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path / "notes")
    before = snapshot(root)

    result, fake = deep(root, monkeypatch=monkeypatch)

    assert result.exit_code == 0, result.output
    assert "Project: notes" in result.stdout
    assert f"Codebase map: {map_path(root.resolve())} (describes the current tree)" in result.stdout
    assert snapshot(root) == before
    text = map_path(root).read_text()
    assert "# Codebase map: notes" in text and "A small notes application." in text
    assert any(r.synthesis for r in fake.requests)
    (manifest,) = RunStore(root.resolve()).list_runs()
    assert manifest.mode == "analyze" and manifest.status == "succeeded"
    assert (root / ".engineering-team" / "repo-profile.json").is_file()
    assert not (root / ".engineering-team" / "baseline.json").exists()  # analyze never runs checks


def test_deep_json_has_the_run_the_profile_and_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)
    use_runner(monkeypatch)

    result = analyze("--repo", str(root), "--deep", json_output=True)

    data = json.loads(result.stdout)
    assert result.exit_code == 0 and data["deep"] is True and data["status"] == "succeeded"
    assert data["profile"]["name"] == "notes" and data["run_id"]
    assert data["map"]["current"] is True and data["map"]["path"].endswith("codebase-map.md")


def test_a_second_deep_run_costs_nothing_until_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)
    first, _ = deep(root, monkeypatch=monkeypatch)
    assert first.exit_code == 0

    again, fake = deep(root, monkeypatch=monkeypatch)
    assert again.exit_code == 0 and fake.requests == []

    refreshed, fake = deep(root, "--refresh", monkeypatch=monkeypatch)
    assert refreshed.exit_code == 0 and fake.requests


def test_an_out_of_date_map_is_flagged_by_a_plain_analyze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)
    deep(root, monkeypatch=monkeypatch)
    (root / "app" / "more.py").write_text("x = 1\n")

    result = analyze("--repo", str(root))

    assert "(out of date: run with --deep)" in result.stdout


def test_a_failed_deep_analysis_exits_1_and_writes_no_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)

    result, _ = deep(root, monkeypatch=monkeypatch, fake=FakeRunner(fail={"map": 99}))

    assert result.exit_code == 1 and not map_path(root).exists()
    (manifest,) = RunStore(root.resolve()).list_runs()
    assert manifest.status == "failed"


def test_deep_needs_credentials_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_tree(tmp_path / "notes", PYTHON_APP)
    monkeypatch.delenv("OPENAI_API_KEY")

    result = analyze("--repo", str(root), "--deep")

    assert result.exit_code == 2 and "OPENAI_API_KEY" in result.stderr
    assert not (root / ".engineering-team").exists()


def test_two_analyses_cannot_run_at_once_in_one_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.runtime.locks import WorkspaceLock

    root = write_tree(tmp_path / "notes", PYTHON_APP)
    held = WorkspaceLock(root).acquire("someone-else")
    try:
        result, _ = deep(root, monkeypatch=monkeypatch)
    finally:
        held.release()

    assert result.exit_code == 2 and "someone-else" in result.stderr
