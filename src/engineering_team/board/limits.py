"""Limits and the fields the controller may update on a card."""

from __future__ import annotations

from engineering_team.board.rules import BoardError

BOARD_FILENAME = "board.json"
BOARD_MARKDOWN_FILENAME = "board.md"

MAX_CARDS = 500
MAX_COMMENTS_PER_CARD = 100
MAX_TITLE = 200
MAX_TEXT = 4000
MAX_REASON = 500
MAX_PROGRESS_NOTE = 200

# Fields the controller may change with ``update`` (everything else changes through a move).
UPDATABLE = frozenset(
    {
        "title",
        "description",
        "assignee",
        "lane",
        "stage",
        "depends_on",
        "criteria_ids",
        "owned_paths",
        "artifacts",
        "tokens",
        "cost_usd",
    }
)
_LIST_FIELDS = frozenset({"depends_on", "criteria_ids", "owned_paths", "artifacts"})


def limit(name: str, value: str, maximum: int) -> str:
    value = value.strip()
    if not value:
        raise BoardError(f"{name} must not be empty.")
    if len(value) > maximum:
        raise BoardError(f"{name} is {len(value)} characters; the limit is {maximum}. Shorten it.")
    return value
