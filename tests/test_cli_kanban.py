"""The kanban renders one board two ways: columns when wide, a list when narrow."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console

from engineering_team.board.models import Card
from engineering_team.cli.kanban import board_columns, format_age, render_kanban

GOLDEN = Path(__file__).parent / "golden"
NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def card(id: str, title: str, status: str, **fields: object) -> Card:
    values: dict[str, object] = {
        "id": id,
        "title": title,
        "kind": "work_package",
        "status": status,
        "created": NOW - timedelta(minutes=30),
    }
    values.update(fields)
    return Card(**values)  # type: ignore[arg-type]


def sample_board() -> list[Card]:
    ago = lambda minutes: NOW - timedelta(minutes=minutes)  # noqa: E731
    return [
        card("K-001", "Specification", "done", kind="stage", started=ago(30), finished=ago(25)),
        card(
            "K-002",
            "Architecture and plan",
            "done",
            kind="stage",
            started=ago(25),
            finished=ago(20),
        ),
        card(
            "K-003",
            "Storage layer",
            "in_progress",
            assignee="backend_engineer",
            lane=1,
            started=ago(12),
        ),
        card(
            "K-004",
            "Command line interface",
            "in_progress",
            assignee="backend_engineer",
            lane=2,
            started=ago(3),
        ),
        card("K-005", "Integration", "backlog", kind="stage"),
        card("K-006", "Release report", "ready", kind="stage"),
        card("K-007", "Project tests", "verifying", kind="check", started=ago(1)),
        card(
            "K-008",
            "Web front end",
            "blocked",
            assignee="frontend_engineer",
            started=ago(9),
            blocked_reason="needs the API contract from K-003",
        ),
        card("K-009", "A note from you", "done", kind="user_note"),
    ]


def render(width: int) -> str:
    console = Console(width=width, color_system=None, record=True, file=io.StringIO())
    console.print(render_kanban(sample_board(), width=width, now=NOW))
    return console.export_text()


@pytest.mark.parametrize(("width", "name"), [(150, "kanban_wide.txt"), (60, "kanban_narrow.txt")])
def test_the_board_matches_its_golden_rendering(width: int, name: str) -> None:
    assert render(width) == (GOLDEN / name).read_text(encoding="utf-8")


def test_a_wide_terminal_gets_columns_and_a_narrow_one_a_list() -> None:
    wide, narrow = render(150), render(60)

    assert "Backlog (1)" in wide.splitlines()[0] and "In progress (2)" in wide.splitlines()[0]
    assert "Backlog (1)" in narrow.splitlines()[0] and len(narrow.splitlines()) > 12
    assert all(len(line) <= 60 for line in narrow.splitlines())
    assert "⛔ needs the API contract from K-003" in narrow  # blocked cards say why


def test_user_notes_and_cancelled_cards_are_not_on_the_board() -> None:
    cards = [*sample_board(), card("K-010", "Dropped", "cancelled")]

    shown = [c.id for column in board_columns(cards).values() for c in column]

    assert "K-009" not in shown and "K-010" not in shown


def test_a_failed_column_appears_only_when_something_failed() -> None:
    assert "failed" not in board_columns(sample_board())
    assert "failed" in board_columns([*sample_board(), card("K-011", "Broken", "failed")])


def test_old_done_cards_are_summarised() -> None:
    done = [card(f"K-{n:03d}", f"Task {n}", "done") for n in range(1, 11)]
    console = Console(width=150, color_system=None, record=True, file=io.StringIO())

    console.print(render_kanban(done, width=150, now=NOW))

    assert "… +4 earlier" in console.export_text()


def test_an_empty_board_says_so() -> None:
    console = Console(width=80, color_system=None, record=True, file=io.StringIO())

    console.print(render_kanban([], width=80, now=NOW))

    assert "board is empty" in console.export_text()


@pytest.mark.parametrize(
    ("seconds", "text"), [(5, "5s"), (59, "59s"), (60, "1m"), (3599, "59m"), (3660, "1h01m")]
)
def test_ages_are_short(seconds: int, text: str) -> None:
    assert format_age(seconds) == text
