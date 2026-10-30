"""The team strategy's parts without a model: the lead's recipe, the delegator's limits, signals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from engineering_team.contracts import CheckResult, ReviewReport
from engineering_team.pipeline.board_sync import StageBoard
from engineering_team.pipeline.delegation import Delegator
from engineering_team.pipeline.delegation_briefs import Job, delegation_cap
from engineering_team.pipeline.lead import lead_recipe, resumed_note, security_signals
from engineering_team.pipeline.lead_contracts import DelegationRecord, LeadRecord
from engineering_team.pipeline.recipes import CONDITIONS, RecipeError, load_recipe
from engineering_team.pipeline.stages import StageOutput, stage_groups
from engineering_team.pipeline.state import PipelineState
from engineering_team.pipeline.strategies import get_strategy
from engineering_team.runtime.context import RunContext
from engineering_team.tools import build_tools
from engineering_team.tools.support import ToolError
from engineering_team.tools.team_tools import NOT_THE_LEAD

MakeContext = Callable[..., RunContext]
LEAD = "generalist_engineer"


# -- the lead's recipe ---------------------------------------------------------------------------


def test_a_new_project_becomes_lead_verify_and_the_controllers_write_up() -> None:
    recipe = lead_recipe(load_recipe("new"))

    assert [(s.name, s.kind) for s in recipe.stages] == [
        ("lead", "lead"),
        ("verify", "verify"),
        ("security_review", "review"),
        ("release_notes", "controller"),
    ]
    lead, verify, security = recipe.stages[:3]
    assert lead.teammates == [LEAD] and lead.file_outputs == ["README.md"]
    assert verify.teammates == [LEAD]  # the lead's teammate repairs, no extra teammate
    assert verify.inputs == []  # nobody produces a spec or a plan any more
    assert security.teammates == ["security_engineer"]
    assert security.skip_if == ["no_security_signal"] and security.optional
    assert recipe.name == "team-new" and recipe.mode == "new"


def test_a_fix_keeps_the_controller_skeleton_and_the_reproduction() -> None:
    recipe = lead_recipe(load_recipe("fix"))

    assert [s.name for s in recipe.stages] == [
        "profile", "baseline", "reproduce", "lead", "verify", "security_review", "summary",
    ]  # fmt: skip
    reproduce = recipe.stage("reproduce")
    assert reproduce.inputs == ["request"]  # triage is the lead's work now
    assert reproduce.teammates == [LEAD]
    assert recipe.stage("lead").inputs == ["request", "repro"]
    assert recipe.policies == load_recipe("fix").policies
    assert recipe.stage("security_review").prompt == "review_fix"


def test_a_recipe_without_verification_cannot_be_led() -> None:
    with pytest.raises(RecipeError, match="no verify stage"):
        lead_recipe(load_recipe("review"))


def test_the_lead_recipe_is_deterministic_so_a_run_resumes_under_it() -> None:
    strategy = get_strategy("team")

    first = strategy.recipe_for(load_recipe("feature"))
    assert first.digest == strategy.recipe_for(load_recipe("feature")).digest
    assert strategy.recipe_for(None).name == "team-new"
    assert first.digest != load_recipe("feature").digest


def test_the_security_review_runs_only_for_a_signal_nobody_reviewed() -> None:
    skip = CONDITIONS["no_security_signal"]
    state = PipelineState()
    settings: Any = None

    assert skip(state, settings)
    state.lead = LeadRecord(signals=[], handed_back="r1")
    assert skip(state, settings)
    state.lead = LeadRecord(signals=["auth (request)"], handed_back="r1")
    assert not skip(state, settings)
    state.lead.security_reviewed = "r0"  # a review of an earlier tree does not count
    assert not skip(state, settings)
    state.lead.security_reviewed = "r1"
    assert skip(state, settings)


def test_security_signals_come_from_the_request_and_the_changed_files(
    make_context: MakeContext,
) -> None:
    ctx = make_context()

    assert security_signals(ctx, "Build a tiny notes CLI.") == []
    assert security_signals(ctx, "Add a login page.") == ["auth (request)"]
    ctx.workspace.write_file("app/run.py", "import subprocess\nsubprocess.run(['ls'])\n")
    assert security_signals(ctx, "Build a tiny notes CLI.") == ["subprocess (app/run.py)"]


# -- the delegator -------------------------------------------------------------------------------


class Jobs:
    """A job runner that records what it was asked and writes what each job's script says."""

    def __init__(self, ctx: RunContext, writes: dict[str, list[str]] | None = None) -> None:
        self.ctx, self.writes, self.jobs = ctx, writes or {}, []  # type: ignore[var-annotated]

    def __call__(self, job: Job) -> StageOutput:
        self.jobs.append(job)
        for path in self.writes.get(job.teammate, []):
            self.ctx.workspace.write_file(path, "VALUE = 1\n")
        if job.kind in ("review", "security_review"):
            return StageOutput(contracts={"review": ReviewReport(summary="Clean.")}, summary="ok")
        return StageOutput(summary=f"{job.teammate} did it")


def delegator(
    ctx: RunContext, jobs: Jobs, *, cap: int = 5, state: PipelineState | None = None
) -> tuple[Delegator, PipelineState]:
    recipe = lead_recipe(load_recipe("new"))
    state = state or PipelineState(lead=LeadRecord(cap=cap))
    board = StageBoard(ctx, recipe, state)
    board.ensure_stage_cards()
    checks = ["- tests: passed"]
    found = Delegator(
        ctx,
        state,
        board,
        lead=LEAD,
        stage="lead",
        cap=cap,
        run_job=jobs,
        checks=lambda: checks[0],
        save=lambda: None,
    )
    return found, state


def brief(teammate: str, paths: list[str], text: str = "Build it.") -> dict[str, Any]:
    return {"teammate": teammate, "brief": text, "owned_paths": paths, "why": "a part"}


def test_a_delegation_is_a_card_a_record_and_a_compact_result(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    jobs = Jobs(ctx, {"backend_engineer": ["src/store.py"]})
    found, state = delegator(ctx, jobs)

    result = found.handle("Delegate", brief("backend_engineer", ["src/**"]))

    (record,) = state.delegations
    assert record.id == "D1" and record.status == "succeeded"
    assert record.files_changed == ["src/store.py"] and record.checks == "- tests: passed"
    assert "Files changed: src/store.py" in result and "Delegations used: 1 of 5." in result
    assert "its own words, not evidence" in result
    card = ctx.board.get(record.card_id or "")
    assert (card.kind, card.status, card.assignee, card.lane) == (
        "delegation", "done", "backend_engineer", "D1",
    )  # fmt: skip
    assert card.parent_id == state.stage_cards["lead"]
    assert "Why (the lead): a part" in card.description
    assert "You may change only: src/**" in jobs.jobs[0].brief


def test_parallel_jobs_need_disjoint_paths(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    jobs = Jobs(ctx)
    found, state = delegator(ctx, jobs)

    with pytest.raises(ToolError, match="may overlap"):
        found.handle(
            "Delegate Parallel",
            {
                "briefs": [
                    brief("backend_engineer", ["src/**"]),
                    brief("frontend_engineer", ["src/ui/**"]),
                ]
            },
        )
    assert state.delegations == [] and jobs.jobs == []  # refused before anything ran

    found.handle(
        "Delegate Parallel",
        {"briefs": [brief("backend_engineer", ["api/**"]), brief("frontend_engineer", ["web/**"])]},
    )
    assert [(r.id, r.teammate, r.status) for r in state.delegations] == [
        ("D1", "backend_engineer", "succeeded"),
        ("D2", "frontend_engineer", "succeeded"),
    ]


def test_the_cap_counts_every_job_and_refuses_past_it(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    found, state = delegator(ctx, Jobs(ctx), cap=2)

    found.handle("Ask Reviewer", {})
    with pytest.raises(ToolError, match="Delegation limit reached: 1 of 2 used, 2 asked"):
        found.handle(
            "Delegate Parallel",
            {"briefs": [brief("backend_engineer", ["a/**"]), brief("frontend_engineer", ["b/**"])]},
        )
    found.handle("Ask Architect", {"question": "One module or two?"})
    with pytest.raises(ToolError, match="limit reached"):
        found.handle("Ask Analyst", {"question": "What does list print?"})
    assert len(state.delegations) == 2


def test_asks_go_to_their_teammate_and_check_what_they_are_given(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    jobs = Jobs(ctx)
    found, state = delegator(ctx, jobs)
    state.checks = [CheckResult(id="tests", name="tests", kind="test", status="failed")]

    with pytest.raises(ToolError, match="Known checks: tests"):
        found.handle("Ask Debugger", {"check_id": "lint"})
    with pytest.raises(ToolError, match="owned_paths"):
        found.handle("Delegate", {"teammate": "backend_engineer", "brief": "x", "owned_paths": []})
    with pytest.raises(ToolError, match="cannot write files"):
        found.handle("Delegate", brief("code_reviewer", ["src/**"]))
    with pytest.raises(ToolError, match="cannot be called in this run"):
        found.handle("Delegate", brief("nobody", ["src/**"]))
    found.handle("Ask Debugger", {"check_id": "tests"})
    found.handle("Ask Security Review", {"focus": "login"})

    assert [(j.teammate, j.prompt, j.owned_paths) for j in jobs.jobs] == [
        ("debugger", "delegate_debug", ()),
        ("security_engineer", "delegate_review", ()),
    ]
    assert "You can only read" in jobs.jobs[0].brief
    assert state.lead is not None and state.lead.security_reviewed is not None


def test_a_failing_job_is_reported_and_does_not_stop_the_lead(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.team.mode = "new"

    def broken(job: Job) -> StageOutput:
        raise RuntimeError("the teammate's model went away")

    found, state = delegator(ctx, Jobs(ctx))
    found._run_job = broken  # type: ignore[method-assign]
    result = found.handle("Delegate", brief("backend_engineer", ["src/**"]))

    assert state.delegations[0].status == "failed"
    assert "Error: RuntimeError: the teammate's model went away" in result
    assert ctx.board.get(state.delegations[0].card_id or "").status == "failed"


def test_teammates_cannot_delegate(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    found, _ = delegator(ctx, Jobs(ctx))

    def tool(agent: str, lane: str | None) -> Any:
        tools = build_tools(ctx, groups=["team"], agent=agent, lane=lane, lazy=False)
        return next(t for t in tools if t.name == "Ask Reviewer")

    with ctx.delegation.opened(found):
        assert tool("backend_engineer", "D1").run() == f"ERROR: {NOT_THE_LEAD}"
        assert tool(LEAD, "D1").run() == f"ERROR: {NOT_THE_LEAD}"  # a delegated generalist
        assert tool(LEAD, None).run().startswith("D1 code_reviewer succeeded")
        # A job is running: nothing can delegate meanwhile.
        with found._busy, pytest.raises(ToolError, match="depth 1"):
            found.handle("Ask Reviewer", {})
    assert tool(LEAD, None).run() == f"ERROR: {NOT_THE_LEAD}"  # outside the lead's session
    assert "team" not in stage_groups(ctx, LEAD)


def test_jobs_an_earlier_session_left_running_are_marked_interrupted(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.team.mode = "new"
    card = ctx.board.create_card("D1", kind="delegation", status="ready")
    ctx.board.move(card.id, "in_progress", actor="controller")
    state = PipelineState(
        lead=LeadRecord(cap=3),
        delegations=[
            DelegationRecord(
                id="D1", kind="delegate", teammate="backend_engineer", brief="Build the store.",
                card_id=card.id, status="running",
            )
        ],
    )  # fmt: skip

    found, _ = delegator(ctx, Jobs(ctx), cap=3, state=state)

    assert state.delegations[0].status == "interrupted"
    assert ctx.board.get(card.id).status == "cancelled"
    assert found.used == 1  # it was spent
    assert "D1 delegate -> backend_engineer: interrupted" in resumed_note(state)
    found.handle("Ask Reviewer", {})
    assert state.delegations[-1].id == "D2"


def test_the_cap_follows_the_size_unless_it_is_set(make_context: MakeContext) -> None:
    ctx = make_context()

    assert [delegation_cap(ctx, size) for size in ("small", "medium", "large", None)] == [
        2, 5, 12, 5,
    ]  # fmt: skip
    fixed = make_context(
        "other", settings=ctx.settings.with_overrides({"delegation.max_delegations": 0}, source="t")
    )
    assert delegation_cap(fixed, "large") == 0
