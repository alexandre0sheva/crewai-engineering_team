from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.contracts import Plan
from engineering_team.pipeline.recipes import (
    CONDITIONS,
    RecipeError,
    bundled_recipes,
    load_recipe,
    parse_recipe,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.settings import load_settings


def test_the_new_recipe_has_the_six_stages_in_order() -> None:
    recipe = load_recipe("new")

    assert [stage.name for stage in recipe.stages] == [
        "spec",
        "plan",
        "foundation",
        "implement",
        "integrate",
        "verify",
        "review",
        "devops",
        "docs",
        "release",
    ]
    assert [stage.kind for stage in recipe.stages] == (
        ["agent"] * 3 + ["parallel", "agent", "verify", "review", "agent", "agent", "agent"]
    )
    # The controller runs the checks; the debugger is only the one who repairs what they find.
    assert recipe.stage("verify").teammates == ["debugger"]
    # Review, DevOps, and docs are optional: a minimal team or no enabled teammate skips them.
    optional = [stage.name for stage in recipe.stages if stage.optional]
    assert optional == ["review", "devops", "docs"]
    assert all(recipe.stage(name).skip_if == ["minimal_team"] for name in optional)
    assert recipe.stage("review").teammates == ["code_reviewer", "security_engineer"]
    assert recipe.stage("verify").file_outputs == []
    assert recipe.stage("plan").contract_outputs == ["plan"]
    assert recipe.stage("plan").file_outputs == ["docs/architecture.md"]
    assert recipe.stage("implement").skip_if == ["no_work_packages"]
    assert "new" in bundled_recipes()


def test_every_teammate_of_the_new_recipe_exists_in_agents_yaml() -> None:
    from engineering_team.pipeline.stages import _yaml

    known = set(_yaml("agents.yaml"))
    for stage in load_recipe("new").stages:
        assert set(stage.teammates) <= known, stage.name
    prompts = set(_yaml("stages.yaml"))
    assert {
        "spec",
        "plan",
        "foundation",
        "implement",
        "integrate",
        "repair",  # the verify stage's agent only repairs
        "repair_review",  # ... and repairs review findings
        "review",
        "devops",
        "docs",
        "release",
        "build",
    } <= prompts


def test_a_recipe_file_can_be_loaded_by_path(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(
        "name: mine\nstages:\n  - name: only\n    teammates: [backend_engineer]\n"
        "    instructions: Do the one thing.\n",
        encoding="utf-8",
    )

    recipe = load_recipe(path)

    assert recipe.name == "mine" and recipe.stages[0].kind == "agent"


def test_the_digest_changes_with_the_recipe() -> None:
    base = "name: r\nstages:\n  - name: a\n    teammates: [x]\n"
    changed = base + "    retry: 2\n"

    assert parse_recipe(base, source="a").digest == parse_recipe(base, source="a").digest
    assert parse_recipe(base, source="a").digest != parse_recipe(changed, source="b").digest


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("name: r\nstages: []\n", "stages"),
        ("name: r\nstages:\n  - name: a\n    kind: agent\n", "at least one teammate"),
        ("name: r\nstages:\n  - name: a\n    kind: controller\n", "needs an action"),
        (
            "name: r\nstages:\n  - {name: a, teammates: [x]}\n  - {name: a, teammates: [x]}\n",
            "duplicate stage name",
        ),
        (
            "name: r\nstages:\n  - {name: a, teammates: [x], inputs: [plan]}\n",
            "no earlier stage produces",
        ),
        (
            "name: r\nstages:\n  - {name: a, teammates: [x], skip_if: [never]}\n",
            "unknown skip_if",
        ),
        (
            "name: r\nstages:\n  - {name: a, teammates: [x], outputs: [banana]}\n",
            "must be one of",
        ),
        (
            "name: r\nstages:\n  - {name: a, teammates: [x], verification_policy: artifacts}\n",
            "no file: outputs",
        ),
        ("name: r\nstages:\n  - {name: A b, teammates: [x]}\n", "lowercase"),
        ("name: r\nstages:\n  - {name: a, teammates: [x], retry: 9}\n", "retry"),
        ("just text", "mapping"),
        ("name: [", "not valid YAML"),
    ],
)
def test_invalid_recipes_are_rejected_with_a_one_line_reason(text: str, message: str) -> None:
    with pytest.raises(RecipeError, match=message):
        parse_recipe(text, source="test.yaml")


def test_an_unknown_recipe_name_lists_the_bundled_ones() -> None:
    with pytest.raises(RecipeError, match="Bundled recipes: .*new"):
        load_recipe("does-not-exist")


def test_the_no_work_packages_condition_looks_at_the_plan() -> None:
    check = CONDITIONS["no_work_packages"]

    settings = load_settings()

    assert check(PipelineState(plan=Plan()), settings) is True
    assert check(PipelineState(), settings) is False  # no plan yet: not a reason to skip


def test_the_minimal_team_condition_follows_the_smoke_profile_and_the_team_profile() -> None:
    check = CONDITIONS["minimal_team"]
    state = PipelineState()

    assert check(state, load_settings()) is False
    assert check(state, load_settings(env={"ENGINEERING_RUN_PROFILE": "smoke"})) is True
    assert check(state, load_settings(env={"ENGINEERING_TEAM_PROFILE": "minimal"})) is True


def test_a_verify_stage_is_the_controllers_and_has_its_own_rules() -> None:
    recipe = parse_recipe(
        "name: r\nstages:\n  - {name: check, kind: verify, teammates: [quality_engineer]}\n",
        source="test",
    )
    assert recipe.stage("check").kind == "verify"

    cases = {
        "teammate": "{name: c, kind: verify}",
        "writes docs/verification.md itself": (
            "{name: c, kind: verify, teammates: [q], outputs: ['file:a.md']}"
        ),
        "cannot be retried": "{name: c, kind: verify, teammates: [q], retry: 1}",
        "only one verify stage": None,
    }
    for message, stage in cases.items():
        stages = (
            "  - {name: a, kind: verify, teammates: [q]}\n"
            "  - {name: b, kind: verify, teammates: [q]}\n"
            if stage is None
            else f"  - {stage}\n"
        )
        with pytest.raises(RecipeError, match=message):
            parse_recipe(f"name: r\nstages:\n{stages}", source="test")
