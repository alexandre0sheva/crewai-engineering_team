"""Deciding, on resume, which stages are finished and which must run again.

A stage is **complete** only if its manifest record says it succeeded (or was skipped), the
contracts it promised are in the pipeline state, **and** the workspace still fits: the tree
hash recorded when it ended is the tree it left behind. A stage that started afterwards may have
changed files, so for the stage that follows the last complete one the check is its recorded
*start* hash; only when that stage never started must the workspace equal the end hash. A
complete stage whose hash no longer fits is run again, and so is everything after it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from engineering_team.contracts import StageRecord
from engineering_team.pipeline.recipes import Recipe, StageSpec
from engineering_team.pipeline.state import PipelineState


class ResumeError(ValueError):
    """A run that cannot be resumed as asked; the message says why and what to do."""


ResumeAction = Literal["reuse", "continue", "rerun", "run"]

RESUME_NOTE = (
    "A previous attempt at this stage may have left partial changes in the workspace. Inspect "
    "the workspace first (list the files, read what exists, run the project's checks) and do "
    "not redo work that is already finished; complete only what is missing or broken."
)

FINISHED = ("succeeded", "skipped")


@dataclass(frozen=True)
class StagePlan:
    """What resume does with one stage. ``reuse``: finished, keep it. ``continue``: it started
    and did not finish, so run it again with the partial-work note and keep finished packages.
    ``rerun``: it finished but the workspace no longer fits, run it again from scratch with the
    note. ``run``: it has not run yet (or follows a stage that runs again)."""

    action: ResumeAction
    reason: str

    @property
    def note(self) -> str | None:
        return RESUME_NOTE if self.action in ("continue", "rerun") else None


def outputs_present(stage: StageSpec, state: PipelineState) -> bool:
    """Whether every contract the stage promises is in the state."""

    return all(getattr(state, name, None) is not None for name in stage.contract_outputs)


def plan_resume(
    recipe: Recipe,
    records: Sequence[StageRecord],
    state: PipelineState,
    current_revision: str,
) -> dict[str, StagePlan]:
    """The action for every stage of ``recipe``, in order (see the module docstring)."""

    by_name = {record.name: record for record in records}
    stages = recipe.stages
    complete = 0
    for stage in stages:
        record = by_name.get(stage.name)
        if record is None or record.status not in FINISHED or not outputs_present(stage, state):
            break
        complete += 1

    invalidated: str | None = None
    while complete:
        tail = by_name[stages[complete - 1].name]
        following = by_name.get(stages[complete].name) if complete < len(stages) else None
        if following is not None and following.revision_start is not None:
            fits = following.revision_start == tail.revision
        else:
            fits = current_revision == tail.revision
        if fits:
            break
        complete -= 1
        invalidated = stages[complete].name

    plan: dict[str, StagePlan] = {}
    for index, stage in enumerate(stages):
        record = by_name.get(stage.name)
        if index < complete:
            plan[stage.name] = StagePlan("reuse", "finished and the workspace still matches")
        elif index == complete:
            if stage.name == invalidated:
                plan[stage.name] = StagePlan(
                    "rerun", "finished, but the workspace changed after it ended"
                )
            elif record is not None and record.revision_start is not None:
                plan[stage.name] = StagePlan("continue", f"it was {record.status}")
            else:
                plan[stage.name] = StagePlan("run", "not started")
        else:
            reason = "follows a stage that runs again" if record else "not started"
            plan[stage.name] = StagePlan("run", reason)
    return plan
