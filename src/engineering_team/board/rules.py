"""Who may move a card where: the one table every board mutation is checked against.

The controller is the source of truth. Agents may start a card, hand it over for verification
("I'm done, please verify"), and block or unblock it; only the controller decides that work is
``done``, ``failed`` or ``cancelled``, or sends a card back after failed checks.
"""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.board.models import (
    CONTROLLER,
    TERMINAL_STATUSES,
    Card,
    CardKind,
    CardStatus,
)

# Kinds that count against the in-progress WIP limit: the cards parallel agents work on. Stage
# cards are containers and subtasks belong to their parent's agent.
WIP_KINDS: frozenset[CardKind] = frozenset({"work_package", "repair"})

AGENT_MOVES: dict[CardStatus, tuple[CardStatus, ...]] = {
    "backlog": ("blocked",),
    "ready": ("in_progress", "blocked"),
    "in_progress": ("verifying", "blocked"),
    "verifying": ("blocked",),
    "blocked": ("in_progress",),
}

CONTROLLER_MOVES: dict[CardStatus, tuple[CardStatus, ...]] = {
    "backlog": ("ready", "blocked", "failed", "cancelled"),
    "ready": ("in_progress", "blocked", "failed", "cancelled"),
    "in_progress": ("verifying", "done", "blocked", "failed", "cancelled"),
    "verifying": ("in_progress", "done", "blocked", "failed", "cancelled"),
    "blocked": ("ready", "in_progress", "failed", "cancelled"),
}

REASON_REQUIRED: frozenset[CardStatus] = frozenset({"blocked", "failed"})


def is_controller(actor: str) -> bool:
    return actor == CONTROLLER


def allowed_moves(status: CardStatus, actor: str) -> tuple[CardStatus, ...]:
    """The statuses ``actor`` may move a card in ``status`` to."""

    table = CONTROLLER_MOVES if is_controller(actor) else AGENT_MOVES
    return table.get(status, ())


class BoardError(ValueError):
    """A board operation that is not allowed; the message says what is."""


def check_move(
    card: Card,
    to: CardStatus,
    actor: str,
    *,
    reason: str,
    evidence: Sequence[str],
    stage_success: bool,
) -> None:
    """Raise :class:`BoardError` unless ``actor`` may move ``card`` to ``to`` as described."""

    allowed = allowed_moves(card.status, actor)
    if to not in allowed:
        raise BoardError(_refusal(card, to, actor, allowed))
    if not is_controller(actor) and card.assignee != actor:
        owner = f"assigned to {card.assignee}" if card.assignee else "not assigned to anyone"
        raise BoardError(
            f"{card.id} is {owner}, not to you ({actor}); you can only move your own cards. "
            f"Add a comment with Comment On Card instead."
        )
    if to in REASON_REQUIRED and not reason.strip():
        raise BoardError(f"Moving {card.id} to {to} needs a reason: say what is {to} and why.")
    if to == "done" and not evidence and not stage_success:
        raise BoardError(
            f"{card.id} cannot be done without evidence: pass the ids of the passing checks, "
            f"or stage_success=True for a stage that succeeded."
        )


def _refusal(card: Card, to: CardStatus, actor: str, allowed: tuple[CardStatus, ...]) -> str:
    options = ", ".join(allowed) if allowed else "none (the card is final)"
    message = f"{card.id} cannot move from {card.status} to {to}. Allowed next states: {options}."
    if card.status in TERMINAL_STATUSES:
        return message
    if not is_controller(actor) and to in TERMINAL_STATUSES:
        message += (
            " Only the controller marks cards done, failed or cancelled; move the card to"
            " verifying to ask for verification."
        )
    elif not is_controller(actor) and card.status == "verifying" and to == "in_progress":
        message += " Only the controller sends a card back after failed checks."
    return message
