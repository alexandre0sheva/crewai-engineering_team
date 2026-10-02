"""Guards the structure of docs/IMPLEMENTATION_PLAN_0.2.0.md and its helper script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("plan_task", ROOT / "scripts" / "plan_task.py")
assert _spec and _spec.loader
plan_task = importlib.util.module_from_spec(_spec)
sys.modules["plan_task"] = plan_task
_spec.loader.exec_module(plan_task)


def _load() -> tuple[str, list]:
    text = plan_task.PLAN.read_text(encoding="utf-8")
    return text, plan_task.parse_index(text)


def test_index_is_contiguous_and_dependencies_point_backwards() -> None:
    _text, rows = _load()

    assert [row.number for row in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        assert all(dep < row.number for dep in row.depends), row


def test_every_indexed_task_has_a_section_with_matching_dependencies() -> None:
    text, rows = _load()

    for row in rows:
        section = plan_task.task_section(text, row.number)
        assert section.startswith(f"#### Task {row.number} — ")
        assert "**Depends on:**" in section
        if row.title and row.number != len(rows):  # the final task depends on "all"
            declared = section.split("**Depends on:** ", 1)[1].split(" ·", 1)[0]
            expected = ",".join(map(str, row.depends)) or "none"
            assert declared == expected, (row.number, declared, expected)


def test_rules_section_is_available_for_the_helper() -> None:
    text, _rows = _load()

    assert "Execution protocol" in plan_task.rules_section(text)
