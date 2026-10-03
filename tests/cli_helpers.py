"""Helpers for CLI tests: drive real runs with the scripted stage runner and look at them."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from pipeline_fakes import REQUEST, FakeRunner, write_checks
from rich.console import Console

from engineering_team import main
from engineering_team.pipeline import strategies
from engineering_team.runtime.run_store import RunStore

ROOT = "ws"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def workspace_root() -> Path:
    return Path.cwd() / ROOT


def project(name: str = "demo") -> Path:
    return workspace_root() / name


def use_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner | None = None) -> FakeRunner:
    runner = runner or FakeRunner()
    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: runner)
    return runner


def new_args(*extra: str, name: str = "demo", request: str = REQUEST) -> list[str]:
    return [
        "new",
        "--request",
        request,
        "--project-name",
        name,
        "--workspace-root",
        str(workspace_root()),
        "--strategy",
        "pipeline",
        "--checks",
        str(write_checks()),
        *extra,
    ]


def run_new(
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    runner: FakeRunner | None = None,
    name: str = "demo",
) -> tuple[int, FakeRunner]:
    """``engineering-team new`` on a scripted run; returns the exit code and the runner."""

    scripted = use_runner(monkeypatch, runner)
    return main.run(new_args(*extra, name=name)), scripted


def latest_run(name: str = "demo") -> str:
    manifest = RunStore(project(name)).latest()
    assert manifest is not None
    return manifest.run_id


def json_out(text: str) -> object:
    """The one JSON document a ``--json`` command wrote."""

    return json.loads(text)


def recording_console(width: int = 120) -> tuple[Console, io.StringIO]:
    """A terminal-like console that keeps what it drew (colour codes included)."""

    sink = io.StringIO()
    return Console(file=sink, force_terminal=True, width=width, color_system="standard"), sink


def plain(text: str) -> str:
    return ANSI.sub("", text)
