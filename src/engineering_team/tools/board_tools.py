"""Group ``board``: the task board as agents see it.

The controller creates cards and decides when work is done; these tools let an agent look at
the board, add subtasks under its own cards, start and hand over its own cards, flag blockers,
comment, and report one-line progress. Every rule is enforced by the ``BoardStore``, not here.
"""

from __future__ import annotations

from collections.abc import Callable

from crewai.tools import BaseTool, tool

from engineering_team.board.models import COLUMNS, Card, CardStatus
from engineering_team.board.rules import BoardError, allowed_moves
from engineering_team.tools.support import ToolEnv, ToolError, bounded

DEFAULT_LIMIT = 40
MAX_LIMIT = 100
KINDS = ("stage", "work_package", "subtask", "repair", "finding", "check")


def board_call(operation: Callable[[], str]) -> Callable[[], str]:
    """Report a refused board operation as a tool error (the message says what is allowed)."""

    def run() -> str:
        try:
            return operation()
        except BoardError as exc:
            raise ToolError(str(exc)) from exc

    return run


def _row(card: Card) -> str:
    detail = f"blocked: {card.blocked_reason}" if card.blocked_reason else card.progress_note
    title = f"{card.title} - {detail}" if detail else card.title
    return (
        f"{card.id:<6} {card.status:<12} {card.kind:<13} {card.assignee or '-':<12} "
        f"{' '.join(title.split())[:110]}"
    )


def _detail(card: Card) -> str:
    lines = [
        f"{card.id} {card.title}",
        f"status: {card.status} · kind: {card.kind} · assignee: {card.assignee or '-'}"
        f" · attempts: {card.attempts}",
    ]
    for label, value in (
        ("stage", card.stage),
        ("parent", card.parent_id),
        ("blocked", card.blocked_reason),
        ("progress", card.progress_note),
        ("depends on", ", ".join(card.depends_on)),
        ("criteria", ", ".join(card.criteria_ids)),
        ("owned paths", ", ".join(card.owned_paths)),
        ("artifacts", ", ".join(card.artifacts)),
        ("evidence", ", ".join(card.evidence)),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if card.description:
        lines += ["", card.description.strip()]
    if card.comments:
        lines += ["", f"comments ({len(card.comments)}, latest {min(len(card.comments), 8)}):"]
        lines += [f"- {c.author}: {' '.join(c.text.split())}" for c in card.comments[-8:]]
    lines += ["", "history:"]
    lines += [
        f"- {m.ts:%H:%M:%S} {m.actor}: {m.from_status or 'created'} -> {m.to_status}"
        + (f" ({m.note})" if m.note else "")
        for m in card.history[-8:]
    ]
    return "\n".join(lines)


def _moved(card: Card, before: CardStatus, actor: str) -> str:
    options = ", ".join(allowed_moves(card.status, actor)) or "none"
    text = f"Moved {card.id}: {before} -> {card.status} (attempt {card.attempts}). Next: {options}."
    if card.status == "verifying":
        text += " The controller verifies it and marks it done, or sends it back with the failures."
    return text


def make_board_tools(env: ToolEnv) -> dict[str, BaseTool]:
    board = env.ctx.board
    actor = env.agent or "agent"

    @tool("List Board Cards")
    def list_board_cards(
        status: str = "", assignee: str = "", kind: str = "", mine: bool = False, limit: int = 40
    ) -> str:
        """List task board cards as a compact table, with overall progress first.

        Filter by status (backlog, ready, in_progress, verifying, blocked, done, failed), kind
        (stage, work_package, subtask, repair, finding, check), assignee, or mine=true for your own
        cards. Call it to find your card ids and see what others are doing.
        """

        def operation() -> str:
            if status and status not in (*COLUMNS, "cancelled"):
                raise ToolError(f"Unknown status {status!r}. Use one of: {', '.join(COLUMNS)}.")
            if kind and kind not in KINDS:
                raise ToolError(f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}.")
            shown = max(1, min(limit, MAX_LIMIT))
            cards = [
                card
                for card in board.cards(
                    status=status or None,
                    kind=kind or None,  # type: ignore[arg-type]
                    assignee=actor if mine else assignee or None,
                )
                if card.kind != "user_note"
            ]
            progress = board.progress()
            columns = " · ".join(f"{name} {progress.by_column[name]}" for name in COLUMNS)
            lines = [
                f"Board: {progress.overall_percent:g}% complete · {progress.cards_done} of "
                f"{progress.cards_total} cards done",
                columns,
                f"Showing {min(shown, len(cards))} of {len(cards)} matching cards:",
                f"{'ID':<6} {'STATUS':<12} {'KIND':<13} {'ASSIGNEE':<12} TITLE",
                *(_row(card) for card in cards[:shown]),
            ]
            if len(cards) > shown:
                lines.append(f"... {len(cards) - shown} more; narrow with status, kind, or mine.")
            return bounded("\n".join(lines))

        return env.run(
            "List Board Cards",
            board_call(operation),
            arguments={"status": status, "assignee": assignee, "kind": kind, "mine": mine},
        )

    @tool("Get Board Card")
    def get_board_card(card_id: str) -> str:
        """Show one card in full: status, assignee, dependencies, owned paths, description, the
        latest comments (including notes from the human), and its history. Example: card_id='K-004'.
        """

        return env.run(
            "Get Board Card",
            board_call(lambda: _detail(board.get(card_id))),
            arguments={"card_id": card_id},
        )

    @tool("Add Subtask")
    def add_subtask(parent_id: str, title: str, description: str = "") -> str:
        """Break your own card into a smaller subtask for yourself; it starts in ready.

        parent_id must be a card assigned to you (leave empty for a standalone subtask). Use it
        to make the plan visible, not for trivia. Move it with Move Card as you work.
        """

        def operation() -> str:
            card = board.create_subtask(parent_id, title, actor=actor, description=description)
            return f"Created {card.id} (subtask, ready) under {card.parent_id or 'the board'}."

        return env.run(
            "Add Subtask",
            board_call(operation),
            arguments={"parent_id": parent_id, "title": title},
        )

    @tool("Move Card")
    def move_card(card_id: str, status: str, reason: str = "") -> str:
        """Move one of your own cards. Start work: ready -> in_progress. Finished: in_progress ->
        verifying (the controller then checks it and marks it done; you cannot mark done or
        failed yourself). Use Block Card to flag a blocker. Example: card_id='K-004',
        status='verifying'.
        """

        def operation() -> str:
            before = board.get(card_id).status
            card = board.move(card_id, status, actor=actor, reason=reason)  # type: ignore[arg-type]
            return _moved(card, before, actor)

        return env.run(
            "Move Card",
            board_call(operation),
            arguments={"card_id": card_id, "status": status, "reason": reason},
        )

    @tool("Block Card")
    def block_card(card_id: str, reason: str) -> str:
        """Flag one of your own cards as blocked, with the reason (what you need, from whom).

        The human and the controller see it on the board. Use it when you cannot continue
        without a decision, a dependency, or a fix outside your files.
        """

        def operation() -> str:
            before = board.get(card_id).status
            return _moved(board.move(card_id, "blocked", actor=actor, reason=reason), before, actor)

        return env.run(
            "Block Card", board_call(operation), arguments={"card_id": card_id, "reason": reason}
        )

    @tool("Unblock Card")
    def unblock_card(card_id: str, note: str = "") -> str:
        """Resume one of your own blocked cards (blocked -> in_progress) once the blocker is gone.

        The optional note says what changed. Fails if too many cards are already in progress.
        """

        def operation() -> str:
            before = board.get(card_id).status
            card = board.move(card_id, "in_progress", actor=actor, reason=note)
            return _moved(card, before, actor)

        return env.run(
            "Unblock Card", board_call(operation), arguments={"card_id": card_id, "note": note}
        )

    @tool("Comment On Card")
    def comment_on_card(card_id: str, text: str) -> str:
        """Add a comment to any card, to hand information to its owner or leave a record.

        Comments are visible to everyone and cannot change the card's status. Keep them short and
        factual. Example: card_id='K-007', text='The API returns 404 for unknown ids.'
        """

        def operation() -> str:
            card = board.comment(card_id, text, author=actor)
            return f"Commented on {card.id} ({len(card.comments)} comments)."

        return env.run(
            "Comment On Card",
            board_call(operation),
            arguments={"card_id": card_id, "text": text},
        )

    @tool("Report Progress")
    def report_progress(card_id: str, status_line: str) -> str:
        """Set the one-line status of one of your own cards, shown in the activity feed.

        Call it at natural milestones, not after every step. Example: card_id='K-004',
        status_line='models and migrations done, writing routes'.
        """

        def operation() -> str:
            card = board.report_progress(card_id, status_line, actor=actor)
            return f"Progress recorded on {card.id}: {card.progress_note}"

        return env.run(
            "Report Progress",
            board_call(operation),
            arguments={"card_id": card_id, "status_line": status_line},
        )

    return {
        "List Board Cards": list_board_cards,
        "Get Board Card": get_board_card,
        "Add Subtask": add_subtask,
        "Move Card": move_card,
        "Block Card": block_card,
        "Unblock Card": unblock_card,
        "Comment On Card": comment_on_card,
        "Report Progress": report_progress,
    }
