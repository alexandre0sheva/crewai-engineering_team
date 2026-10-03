from __future__ import annotations

from collections.abc import Callable

from engineering_team.contracts import Plan, Spec, StageRecord
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.pipeline.resume import RESUME_NOTE, plan_resume
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.snapshot import workspace_revision

RECIPE = load_recipe("new")
NAMES = [stage.name for stage in RECIPE.stages]


def _state(*, spec: bool = True, plan: bool = True) -> PipelineState:
    return PipelineState(
        spec=Spec(title="t") if spec else None, plan=Plan(stack="x") if plan else None
    )


def _chain(upto: int, *, last: str = "succeeded") -> list[StageRecord]:
    """Records for the first ``upto`` stages: each starts at the previous one's end hash."""

    records: list[StageRecord] = []
    for index, name in enumerate(NAMES[:upto]):
        records.append(
            StageRecord(
                name=name,
                status=last if index == upto - 1 else "succeeded",
                revision_start=f"h{index}",
                revision=f"h{index + 1}",
            )
        )
    return records


def actions(plan: dict) -> list[str]:
    return [plan[name].action for name in NAMES]


def test_a_fresh_run_runs_everything() -> None:
    plan = plan_resume(RECIPE, [], _state(spec=False, plan=False), "h0")

    assert actions(plan) == ["run"] * 7


def test_finished_stages_are_reused_while_the_workspace_matches() -> None:
    plan = plan_resume(RECIPE, _chain(3), _state(), "h3")

    assert actions(plan) == ["reuse"] * 3 + ["run"] * 4


def test_an_interrupted_stage_continues_and_earlier_ones_stay_reused() -> None:
    records = [*_chain(3), StageRecord(name="implement", status="interrupted", revision_start="h3")]

    # The interrupted stage changed files, so the workspace no longer equals h3: still reused,
    # because the stage that follows started from exactly that state.
    plan = plan_resume(RECIPE, records, _state(), "partial-work")

    assert actions(plan) == ["reuse"] * 3 + ["continue", "run", "run", "run"]
    assert plan["implement"].note == RESUME_NOTE
    assert plan["verify"].note is None


def test_a_failed_stage_continues() -> None:
    records = [*_chain(1), StageRecord(name="plan", status="failed", revision_start="h1")]

    plan = plan_resume(RECIPE, records, _state(plan=False), "whatever")

    assert actions(plan)[:3] == ["reuse", "continue", "run"]


def test_a_workspace_edited_after_the_last_stage_reruns_that_stage() -> None:
    # Nothing started after "foundation", so the workspace must still be its end state.
    plan = plan_resume(RECIPE, _chain(3), _state(), "edited-by-someone")

    assert actions(plan) == ["reuse", "reuse", "rerun", "run", "run", "run", "run"]
    assert "changed after it ended" in plan["foundation"].reason
    assert plan["foundation"].note == RESUME_NOTE


def test_a_broken_chain_invalidates_back_to_the_stage_that_still_fits() -> None:
    records = _chain(3)
    records[2] = records[2].model_copy(update={"revision_start": "not-h2"})  # edited in between

    plan = plan_resume(RECIPE, records, _state(), "edited")

    # "foundation" started from a tree that is not what "plan" left, so "plan" cannot be trusted
    # either; "spec" is the last stage whose follower started from its end state.
    assert actions(plan)[:3] == ["reuse", "rerun", "run"]


def test_a_stage_whose_contract_is_missing_from_the_state_is_not_complete() -> None:
    plan = plan_resume(RECIPE, _chain(2), _state(plan=False), "h2")

    assert actions(plan)[:3] == ["reuse", "continue", "run"]


def test_a_skipped_stage_counts_as_finished() -> None:
    records = _chain(3)
    records.append(
        StageRecord(
            name="implement", status="skipped", revision_start="h3", revision="h3", detail="x"
        )
    )

    plan = plan_resume(RECIPE, records, _state(), "h3")

    assert actions(plan) == ["reuse"] * 4 + ["run", "run", "run"]


def test_everything_finished_and_matching_is_all_reuse() -> None:
    plan = plan_resume(RECIPE, _chain(7), _state(), "h7")

    assert actions(plan) == ["reuse"] * 7


def test_stale_records_after_the_frontier_run_again() -> None:
    records = [*_chain(2), StageRecord(name="foundation", status="failed", revision_start="h2")]
    records.append(StageRecord(name="verify", status="succeeded", revision_start="x", revision="y"))

    plan = plan_resume(RECIPE, records, _state(), "h2")

    assert plan["verify"].action == "run" and "follows" in plan["verify"].reason


# -- the workspace revision ------------------------------------------------------------------


def test_the_revision_follows_content_and_ignores_controller_state(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context()
    workspace = ctx.workspace
    empty = workspace_revision(workspace)

    workspace.write_file("a.txt", "one")
    first = workspace_revision(workspace)
    workspace.write_file("a.txt", "one")  # same content again
    assert workspace_revision(workspace) == first != empty

    workspace.write_file("a.txt", "two")
    assert workspace_revision(workspace) not in (first, empty)

    workspace.write_file("a.txt", "one")
    assert workspace_revision(workspace) == first  # content, not history

    state_file = workspace.root / ".engineering-team" / "runs" / "x" / "note.txt"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("state", encoding="utf-8")
    workspace.write_file("node_modules/dep/index.js", "ignored")
    assert workspace_revision(workspace) == first


def test_the_revision_notices_a_rename_and_honours_gitignore(
    make_context: Callable[..., RunContext],
) -> None:
    workspace = make_context().workspace
    workspace.write_file(".gitignore", "build/\n")
    workspace.write_file("a.txt", "x")
    before = workspace_revision(workspace)

    workspace.write_file("build/out.bin", "ignored")
    assert workspace_revision(workspace) == before

    (workspace.root / "a.txt").rename(workspace.root / "b.txt")
    assert workspace_revision(workspace) != before


def test_resume_note_text_tells_the_agent_to_inspect_first() -> None:
    assert "Inspect the workspace first" in RESUME_NOTE and "do not redo" in RESUME_NOTE
