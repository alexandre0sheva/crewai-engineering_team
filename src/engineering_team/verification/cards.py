"""One board card per check, moved by the controller as the check runs.

``ready → in_progress → verifying`` while the check runs, then ``done`` (the check's id is the
evidence), ``failed`` (the reason is what the check showed), ``blocked`` (it could not run) or
``cancelled`` (it was skipped). A card that ended ``failed`` is reopened when the check runs
again after a repair; a ``done`` card is final, so a check that regresses gets a new card.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from engineering_team.board.models import CONTROLLER
from engineering_team.board.rules import BoardError
from engineering_team.contracts import CheckResult, CheckSpec
from engineering_team.runtime.context import RunContext

log = logging.getLogger(__name__)

MAX_REASON = 480


class CheckCards:
    """Creates and moves check cards; ``mapping`` (check id → card id) is the caller's to keep."""

    def __init__(
        self, ctx: RunContext, mapping: dict[str, str], *, stage: str, parent: str | None
    ) -> None:
        self.ctx = ctx
        self.board = ctx.board
        self.mapping = mapping
        self.stage = stage
        self.parent = parent

    def start(self, check: CheckSpec) -> None:
        self._guard(check.id, lambda: self._start(check))

    def finish(self, check: CheckSpec, result: CheckResult) -> None:
        card_id = self.mapping.get(check.id)
        if card_id is None:
            return
        reason = (result.hint or result.summary or result.status)[:MAX_REASON]

        def move() -> None:
            self.board.move(card_id, "verifying", actor=CONTROLLER)
            if result.status == "passed":
                self.board.move(card_id, "done", actor=CONTROLLER, evidence=[check.id])
            elif result.status == "failed":
                self.board.move(card_id, "failed", actor=CONTROLLER, reason=reason)
            elif result.status == "unavailable":
                self.board.move(card_id, "blocked", actor=CONTROLLER, reason=reason)
            else:  # skipped
                self.board.move(card_id, "cancelled", actor=CONTROLLER, reason=reason)

        self._guard(check.id, move)

    def open_repair(self, round_no: int, teammate: str, failing: list[str]) -> str | None:
        """A ``repair`` card, in progress, for the agent about to fix ``failing`` checks."""

        holder: list[str] = []

        def create() -> None:
            card = self.board.create_card(
                f"Repair round {round_no}: {', '.join(failing)}"[:200],
                kind="repair",
                status="ready",
                assignee=teammate,
                stage=self.stage,
                parent_id=self.parent,
                description="Fix what the controller's checks or the reviewers found.",
            )
            holder.append(card.id)
            self.board.move(card.id, "in_progress", actor=CONTROLLER)

        self._guard("repair", create)
        return holder[0] if holder else None

    def close_repair(self, card_id: str | None, fixed: list[str], still_failing: list[str]) -> None:
        """The controller's verdict on a repair round: done when every check it was sent for now
        passes (those ids are the evidence), failed otherwise."""

        if card_id is None:
            return

        def move() -> None:
            self.board.move(card_id, "verifying", actor=CONTROLLER)
            if still_failing or not fixed:
                reason = f"still failing after the repair: {', '.join(still_failing) or 'unknown'}"
                self.board.move(card_id, "failed", actor=CONTROLLER, reason=reason[:MAX_REASON])
            else:
                self.board.move(card_id, "done", actor=CONTROLLER, evidence=fixed)

        self._guard("repair", move)

    def close_findings_repair(
        self, card_id: str | None, *, evidence: list[str], reason: str = ""
    ) -> None:
        """A repair round for review findings, closed after the project was verified again: done
        with the ids of the checks that then passed as evidence, or failed with ``reason``. (The
        controller cannot prove a finding itself fixed; the checks are what it re-runs.)"""

        if card_id is None:
            return

        def move() -> None:
            self.board.move(card_id, "verifying", actor=CONTROLLER)
            if reason or not evidence:
                self.board.move(
                    card_id, "failed", actor=CONTROLLER, reason=(reason or "no check passed")
                )
            else:
                self.board.move(card_id, "done", actor=CONTROLLER, evidence=evidence)

        self._guard("repair", move)

    def cancel(self, check: CheckSpec, reason: str) -> None:
        card_id = self.mapping.get(check.id)
        if card_id is not None:
            self._guard(check.id, lambda: self._cancel(card_id, reason))

    # -- internals -----------------------------------------------------------------------

    def _cancel(self, card_id: str, reason: str) -> None:
        if self.board.get(card_id).status not in ("done", "failed", "cancelled"):
            self.board.move(card_id, "cancelled", actor=CONTROLLER, reason=reason)

    def _start(self, check: CheckSpec) -> None:
        card_id = self.mapping.get(check.id)
        status = self.board.get(card_id).status if card_id else None
        if card_id is None or status == "done":
            title = check.name if card_id is None else f"{check.name} (re-run)"
            card = self.board.create_card(
                title[:200],
                kind="check",
                status="ready",
                stage=self.stage,
                parent_id=self.parent,
                description=" ".join(check.argv) or check.script or check.name,
                criteria_ids=list(check.criteria_ids),
            )
            self.mapping[check.id] = card.id
            card_id, status = card.id, "ready"
        if status in ("failed", "cancelled", "backlog"):
            self.board.move(card_id, "ready", actor=CONTROLLER)
        if status != "in_progress":
            self.board.move(card_id, "in_progress", actor=CONTROLLER)

    def _guard(self, check_id: str, action: Callable[[], None]) -> None:
        try:
            action()
        except BoardError as exc:
            log.warning("Board move for check %s refused: %s", check_id, exc)
            self.ctx.events.emit("pipeline.board_warning", check=check_id, error=str(exc)[:300])
