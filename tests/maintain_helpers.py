"""Shared pieces for the maintain, review, and recipe tests: a project, a runner, a way to run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner
from pipeline_fakes import FakeRunner
from repo_fixtures import PYTHON_APP, make_repo
from test_feature_mode import git, latest, state_of  # noqa: F401  (re-exported for the tests)
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.pipeline.stages import StageRequest

runner = CliRunner()
WORKSPACE_ROOT = "ws"


def project(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    return make_repo(tmp_path / "notes", {**PYTHON_APP, **(files or {})})


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    *args: str,
    fake: FakeRunner | None = None,
    before: tuple[str, ...] = (),
) -> tuple[Any, FakeRunner]:
    """``engineering-team [--json ...] <args>`` against a scripted stage runner."""

    scripted = use_runner(monkeypatch, fake or FakeRunner())
    result = runner.invoke(
        app, ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT), *before, *args]
    )
    return result, scripted


def maintain(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    task: str,
    *extra: str,
    fake: FakeRunner | None = None,
    before: tuple[str, ...] = (),
) -> tuple[Any, FakeRunner]:
    return run_cli(
        monkeypatch, "maintain", "--repo", str(repo), "--task", task, *extra,
        fake=fake, before=before,
    )  # fmt: skip


def scripted(**by_stage: Callable[[StageRequest], None]) -> Callable[[StageRequest], None]:
    """An ``on_call`` that runs the function registered for the request's stage name."""

    def on_call(request: StageRequest) -> None:
        action = by_stage.get(request.stage.name)
        if action is not None:
            action(request)

    return on_call


def event_types(ref: Any) -> list[str]:
    lines = (ref.run_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line)["type"] for line in lines]


def check(state: Any, check_id: str) -> Any:
    return next(c for c in state.checks if c.id == check_id)
