"""The controller's side of the task board: cards for stages and work packages, moved by events.

The board stays truthful whatever the agents do: this module creates one ``stage`` card per
recipe stage when the run starts, one ``work_package`` card per package of the architect's plan,
and moves them as stages start, finish, fail, or are cancelled. Every move goes through the
board's own rules as the controller; a refused move is logged and reported as an event instead
of breaking the run (the board is a view, the manifest is the record).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from engineering_team.board.models import CONTROLLER, Card, CardStatus
from engineering_team.board.rules import BoardError
from engineering_team.contracts import Plan
from engineering_team.pipeline.recipes import Recipe, StageSpec
from engineering_team.pipeline.stages import lead_teammate, teammate_for
from engineering_team.pipeline.state import PackageState, PipelineState
from engineering_team.runtime.context import RunContext

log = logging.getLogger(__name__)

MAX_REASON = 480


class StageBoard:
    """Creates and moves the run's stage and work-package cards."""

    def __init__(self, ctx: RunContext, recipe: Recipe, state: PipelineState) -> None:
        self.ctx = ctx
        self.recipe = recipe
        self.state = state
        self.board = ctx.board

    # -- cards ---------------------------------------------------------------------------

    def ensure_stage_cards(self) -> None:
        """One card per stage, in order (first ready, the rest backlog); safe to call again."""

        previous: str | None = None
        for index, stage in enumerate(self.recipe.stages):
            card_id = self.state.stage_cards.get(stage.name)
            if card_id is None or not self._exists(card_id):
                card = self.board.create_card(
                    stage.name.replace("_", " ").capitalize(),
                    kind="stage",
                    status="ready" if index == 0 else "backlog",
                    assignee=lead_teammate(self.ctx, stage) if stage.teammates else None,
                    stage=stage.name,
                    description=stage.description or f"Stage {index + 1}: {stage.kind}",
                    depends_on=[previous] if previous else [],
                )
                self.state.stage_cards[stage.name] = card.id
                card_id = card.id
            previous = card_id

    def ensure_package_cards(self, plan: Plan, host: StageSpec) -> None:
        """One card per work package of ``plan``, under the card of the stage that runs them."""

        parent = self.state.stage_cards.get(host.name)
        created: dict[str, str] = {}
        for package in plan.work_packages:
            known = self.state.packages.setdefault(package.id, PackageState())
            if known.card_id and self._exists(known.card_id):
                created[package.id] = known.card_id
                continue
            card = self.board.create_card(
                f"{package.id}: {package.title}"[:200],
                kind="work_package",
                status="backlog",
                assignee=teammate_for(host, package, self.ctx.team),
                stage=host.name,
                parent_id=parent,
                description=package.description,
                criteria_ids=list(package.criteria_ids),
                owned_paths=list(package.owned_paths),
            )
            known.card_id = card.id
            created[package.id] = card.id
        for package in plan.work_packages:
            dependencies = [created[d] for d in package.depends_on if d in created]
            if dependencies:
                self.board.update(created[package.id], depends_on=dependencies)

    def card_for(self, stage: str) -> str | None:
        return self.state.stage_cards.get(stage)

    def steering(self, teammate: str) -> str:
        """The human's notes this teammate has not seen yet (each is delivered once)."""

        return self.board.take_steering(teammate)

    # -- moves ---------------------------------------------------------------------------

    def start(
        self, card_id: str | None, assignee: str | None = None, lane: int | None = None
    ) -> None:
        """Get a card to ``in_progress`` (in ``lane``), reopening it first if an earlier attempt
        ended it."""

        if card_id is None:
            return
        self._guard(card_id, lambda: self._start(card_id, assignee, lane))

    def verifying(self, card_id: str | None) -> None:
        self._to(card_id, "verifying")

    def done(self, card_id: str | None, note: str = "") -> None:
        self._to(card_id, "done", reason=note, stage_success=True)

    def failed(self, card_id: str | None, reason: str) -> None:
        self._to(card_id, "failed", reason=reason or "the stage failed")

    def cancelled(self, card_id: str | None, reason: str = "") -> None:
        self._to(card_id, "cancelled", reason=reason)

    def promote_next(self) -> None:
        """Mark the first stage that has not started as ``ready``."""

        for stage in self.recipe.stages:
            card_id = self.state.stage_cards.get(stage.name)
            if card_id and self._exists(card_id) and self.board.get(card_id).status == "backlog":
                self._to(card_id, "ready")
                return

    # -- internals -----------------------------------------------------------------------

    def _exists(self, card_id: str) -> bool:
        try:
            self.board.get(card_id)
        except BoardError:
            return False
        return True

    def _start(self, card_id: str, assignee: str | None, lane: int | None) -> None:
        card: Card = self.board.get(card_id)
        if assignee and card.assignee != assignee:
            self.board.update(card_id, assignee=assignee)
        if lane is not None and card.lane != lane:
            self.board.update(card_id, lane=lane)
        if card.status == "done":
            return
        if card.status in ("failed", "cancelled", "backlog"):
            self.board.move(card_id, "ready", actor=CONTROLLER)
            card = self.board.get(card_id)
        if card.status != "in_progress":
            self.board.move(card_id, "in_progress", actor=CONTROLLER)

    def _to(
        self,
        card_id: str | None,
        status: CardStatus,
        *,
        reason: str = "",
        stage_success: bool = False,
    ) -> None:
        if card_id is None:
            return

        def move() -> None:
            if self.board.get(card_id).status == status:
                return  # an agent already handed it over, or it is already there
            self.board.move(
                card_id,
                status,
                actor=CONTROLLER,
                reason=reason[:MAX_REASON],
                stage_success=stage_success,
            )

        self._guard(card_id, move)

    def _guard(self, card_id: str, action: Callable[[], None]) -> None:
        try:
            action()
        except BoardError as exc:
            log.warning("Board move for %s refused: %s", card_id, exc)
            self.ctx.events.emit("pipeline.board_warning", card_id=card_id, error=str(exc)[:300])
