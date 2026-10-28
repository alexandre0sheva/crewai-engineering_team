"""The lean team's decisions: task size, ceremony, plan collapse, reviewers and tiers."""

from __future__ import annotations

import pytest

from engineering_team.contracts import Plan, WorkPackage
from engineering_team.pipeline.lean import (
    LARGE_REQUEST_WORDS,
    LEAN_CONDITIONS,
    LeanProfile,
    assess,
    collapse_plan,
    refine,
    reviewers,
    tier_for,
)
from engineering_team.pipeline.recipes import CONDITIONS, load_recipe
from engineering_team.pipeline.state import PipelineState
from engineering_team.settings import load_settings


def plan_of(count: int) -> Plan:
    return Plan(
        stack="python",
        work_packages=[
            WorkPackage(
                id=f"WP-{n}",
                title=f"Part {n}",
                role="backend",
                description=f"Builds part {n}.",
                owned_paths=[f"src/part{n}/**"],
                depends_on=[f"WP-{n - 1}"] if n > 1 else [],
                criteria_ids=[f"AC-{n}"],
                required=n != 2,
            )
            for n in range(1, count + 1)
        ],
    )


def test_a_short_request_for_a_new_project_is_small_and_asks_for_no_ceremony() -> None:
    profile = assess("Build a tiny notes CLI with add and list commands.", load_settings(), 0)

    assert (profile.size, profile.high_risk, profile.ceremony) == ("small", False, [])
    assert profile.reasons == ["request: 10 words"]


def test_a_long_request_or_a_big_repository_makes_the_task_larger() -> None:
    settings = load_settings()

    assert assess("word " * (LARGE_REQUEST_WORDS + 1), settings, 0).size == "large"
    assert assess("Add a flag.", settings, 41).size == "medium"
    assert assess("Add a flag.", settings, 401).size == "large"
    assert assess("Add a flag.", settings, 40).size == "small"


def test_the_request_asks_for_ceremony_and_the_setting_can_force_it() -> None:
    asked = assess(
        "Build an API with a Dockerfile and GitHub Actions CI, plus user docs.", load_settings(), 0
    )

    assert asked.ceremony == ["devops", "docs"]
    assert assess(
        "Build a CLI.", load_settings(overrides={"lean.ceremony": "always"}), 0
    ).ceremony == [
        "devops",
        "docs",
    ]
    assert (
        assess(
            "Deploy it with Docker.", load_settings(overrides={"lean.ceremony": "never"}), 0
        ).ceremony
        == []
    )


def test_a_security_sensitive_request_is_high_risk() -> None:
    profile = assess("Add a login page with password reset.", load_settings(), 0)

    assert profile.high_risk
    assert "high risk" in profile.reasons[-1]


def test_lean_size_overrides_every_signal() -> None:
    settings = load_settings(overrides={"lean.size": "large"})

    assert assess("Tiny.", settings, 0).size == "large"
    assert refine(LeanProfile(size="large"), plan_of(1), settings).size == "large"


def test_the_plan_makes_a_task_larger_never_smaller() -> None:
    settings = load_settings()
    small = LeanProfile(size="small")

    assert refine(small, plan_of(2), settings) is small
    medium = refine(small, plan_of(3), settings)
    assert medium.size == "medium" and medium.reasons[-1] == "plan: 3 work packages"
    assert refine(small, plan_of(6), settings).size == "large"
    assert refine(LeanProfile(size="large"), plan_of(1), settings).size == "large"


def test_a_collapsed_plan_is_one_package_that_owns_everything_and_keeps_the_split() -> None:
    plan = plan_of(3)

    collapsed = collapse_plan(plan)

    (package,) = collapsed.work_packages
    assert package.id == "WP-1" and not package.depends_on
    assert package.owned_paths == ["src/part1/**", "src/part2/**", "src/part3/**"]
    assert package.criteria_ids == ["AC-1", "AC-2", "AC-3"]
    assert package.required
    assert "WP-2 Part 2 (src/part2/**): Builds part 2." in package.description
    assert collapsed.stack == plan.stack
    assert collapse_plan(plan_of(1)) == plan_of(1)


def test_a_small_low_risk_review_keeps_one_reviewer() -> None:
    both = ["code_reviewer", "security_engineer"]

    assert reviewers(LeanProfile(size="small"), both) == ["code_reviewer"]
    assert reviewers(LeanProfile(size="small"), ["security_engineer"]) == ["security_engineer"]
    assert reviewers(LeanProfile(size="small", high_risk=True), both) == both
    assert reviewers(LeanProfile(size="medium"), both) == both
    assert reviewers(None, both) == both


def test_the_merged_design_and_a_small_review_run_on_the_worker_tier() -> None:
    small, medium = LeanProfile(size="small"), LeanProfile(size="medium")

    assert tier_for(small, "agent", ["spec", "plan"]) == "worker"
    assert tier_for(medium, "agent", ["spec", "plan"]) == "worker"
    assert tier_for(small, "review", []) == "worker"
    assert tier_for(medium, "review", []) is None
    assert tier_for(small, "agent", ["plan"]) is None
    assert tier_for(LeanProfile(size="large"), "agent", ["spec", "plan"]) is None
    assert tier_for(LeanProfile(size="small", high_risk=True), "review", []) is None


@pytest.mark.parametrize(
    ("size", "skipped"),
    [
        ("small", {"spec", "plan", "devops", "docs"}),
        ("medium", {"spec", "plan", "devops", "docs"}),
        ("large", {"design", "devops", "docs"}),
    ],
)
def test_the_new_recipe_skips_by_size_and_ceremony(size: str, skipped: set[str]) -> None:
    recipe, settings = load_recipe("new"), load_settings()
    state = PipelineState(lean=LeanProfile(size=size))  # type: ignore[arg-type]

    found = {
        stage.name
        for stage in recipe.stages
        if any(CONDITIONS[name](state, settings) for name in stage.skip_if)
    }

    assert found == skipped
    assert set(LEAN_CONDITIONS) <= set(CONDITIONS)


def test_a_one_package_plan_skips_foundation_and_integration() -> None:
    recipe, settings = load_recipe("new"), load_settings()
    state = PipelineState(lean=LeanProfile(size="small"), plan=plan_of(1))

    skipped = {
        stage.name
        for stage in recipe.stages
        if any(CONDITIONS[name](state, settings) for name in stage.skip_if)
    }

    assert {"foundation", "integrate"} <= skipped
    assert next(s for s in recipe.stages if s.name == "implement").solo_prompt == "implement_solo"


def test_requested_ceremony_runs() -> None:
    recipe, settings = load_recipe("new"), load_settings()
    state = PipelineState(lean=LeanProfile(size="small", ceremony=["devops", "docs"]))

    skipped = {
        stage.name
        for stage in recipe.stages
        if any(CONDITIONS[name](state, settings) for name in stage.skip_if)
    }

    assert not {"devops", "docs"} & skipped


def test_a_hand_off_pack_lists_recent_files_with_hashes_and_says_how_many_more(
    make_context,  # type: ignore[no-untyped-def]
) -> None:
    from engineering_team.contracts import CheckResult
    from engineering_team.pipeline.handoff import MAX_FILES, handoff_pack

    ctx = make_context()
    for number in range(MAX_FILES + 5):
        ctx.workspace.write_file(f"src/m{number:03}.py", f"V = {number}\n")
    state = PipelineState(
        checks=[CheckResult(id="tests", status="failed", summary="1 failed")],
        summaries={"design": "Planned it.", "implement": "Built it."},
    )

    pack = handoff_pack(ctx, state, load_recipe("new").stage("implement"))

    lines = pack.splitlines()
    assert lines[0] == "Hand-off from the controller:"
    assert sum(1 for line in lines if line.startswith("- src/m")) == MAX_FILES
    assert "- ... and 5 more (Find Files or Project Tree list them)" in lines
    assert "- tests: failed (1 failed)" in lines
    assert "- design: Planned it." in lines and "- implement: Built it." not in lines
    assert (
        handoff_pack(make_context("empty"), PipelineState(), load_recipe("new").stage("design"))
        == ""
    )
