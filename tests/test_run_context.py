from __future__ import annotations

import re
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.settings import load_settings
from engineering_team.tools.workspace import ProjectWorkspace


def test_run_ids_are_unique_and_sort_by_creation_time() -> None:
    first = new_run_id()
    second = new_run_id()

    assert first != second
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", first)
    assert new_run_id() >= first[:15]


def test_the_run_directory_lives_under_the_controller_state(tmp_path: Path) -> None:
    workspace = ProjectWorkspace.create(tmp_path / "project")

    ctx = RunContext.create(load_settings(), workspace, run_id="20260102-030405-abcdef")

    assert ctx.run_dir == workspace.root / ".engineering-team" / "runs" / "20260102-030405-abcdef"
    assert ctx.run_dir.is_dir()


def test_the_context_is_frozen_and_carries_the_command_gate(tmp_path: Path) -> None:
    settings = load_settings(overrides={"execution.max_parallel_commands": 3})
    ctx = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "project"))

    assert isinstance(ctx.cancel_event, threading.Event)
    assert not ctx.cancel_event.is_set()
    # A BoundedSemaphore(3) admits exactly three holders.
    assert [ctx.command_gate.acquire(blocking=False) for _ in range(4)] == [True] * 3 + [False]
    with pytest.raises(FrozenInstanceError):
        ctx.run_id = "other"  # type: ignore[misc]


def test_every_context_gets_its_own_cancel_event_and_gate(tmp_path: Path) -> None:
    settings = load_settings()
    first = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "a"))
    second = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "b"))

    first.cancel_event.set()

    assert not second.cancel_event.is_set()
    assert first.command_gate is not second.command_gate
