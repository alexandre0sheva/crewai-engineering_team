"""Group ``human``: asking the person running the team a question."""

from __future__ import annotations

from crewai.tools import BaseTool, tool

from engineering_team.board.rules import BoardError
from engineering_team.tools.support import ToolEnv, ToolError

MIN_TIMEOUT = 5
MAX_TIMEOUT = 900
NO_HUMAN = (
    "No human is available to answer - proceed with your best assumption and record it "
    "with Write Note (or Log Decision for a lasting choice)."
)


def make_human_tools(env: ToolEnv) -> dict[str, BaseTool]:
    ctx = env.ctx
    actor = env.agent or "agent"

    def ask(question: str, card_id: str, timeout_seconds: int) -> str:
        if not question.strip():
            raise ToolError("Ask a specific question; the text was empty.")
        timeout = float(max(MIN_TIMEOUT, min(timeout_seconds, MAX_TIMEOUT)))
        waiting = _block_card(env, card_id, question) if ctx.human.interactive else None
        try:
            answer = ctx.human.ask(
                question.strip(),
                agent=actor,
                card_id=card_id or None,
                timeout=timeout,
                cancel_event=ctx.cancel_event,
            )
        finally:
            resumed = _resume_card(env, waiting)
        if answer is None:
            if not ctx.human.interactive:
                return NO_HUMAN
            return (
                f"The human did not answer within {timeout:.0f} seconds - proceed with your best "
                f"assumption and record it with Write Note.{resumed}"
            )
        return f"The human answered: {answer}{resumed}"

    @tool("Ask Human")
    def ask_human(question: str, card_id: str = "", timeout_seconds: int = 300) -> str:
        """Ask the person running this team a question and wait for the answer.

        Use it only for decisions you cannot reasonably assume (requirements that change the
        design); never for things you can look up or decide. Give card_id to mark your card
        blocked while you wait. If nobody answers, proceed with your best assumption and record it.
        """

        return env.run(
            "Ask Human",
            lambda: ask(question, card_id, timeout_seconds),
            arguments={"question": question, "card_id": card_id},
        )

    return {"Ask Human": ask_human}


def _block_card(env: ToolEnv, card_id: str, question: str) -> str | None:
    """Mark the agent's own in-progress card blocked while it waits; ``None`` if it cannot."""

    if not card_id:
        return None
    board, actor = env.ctx.board, env.agent or "agent"
    try:
        card = board.get(card_id)
        if card.assignee != actor or card.status != "in_progress":
            return None
        reason = f"waiting for the human: {' '.join(question.split())[:120]}"
        board.move(card.id, "blocked", actor=actor, reason=reason)
    except BoardError:
        return None
    return card.id


def _resume_card(env: ToolEnv, card_id: str | None) -> str:
    """Put a card blocked by :func:`_block_card` back in progress; a note if that is refused."""

    if card_id is None:
        return ""
    try:
        env.ctx.board.move(
            card_id, "in_progress", actor=env.agent or "agent", reason="human replied"
        )
    except BoardError as exc:
        return f" (Could not resume {card_id}: {exc})"
    return ""
