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


def test_the_new_recipe_has_the_six_stages_in_order() -> None:
    recipe = load_recipe("new")

    assert [stage.name for stage in recipe.stages] == [
        "spec",
        "plan",
        "foundation",
        "implement",
        "integrate",
        "verify",
        "release",
    ]
    assert [stage.kind for stage in recipe.stages] == ["agent"] * 3 + ["parallel"] + ["agent"] * 3
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
        "verify",
        "release",
        "build",
    } <= prompts


def test_a_recipe_file_can_be_loaded_by_path(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(
        "name: mine\nstages:\n  - name: only\n    teammates: [backend_engineer]\n",
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

    assert check(PipelineState(plan=Plan())) is True
    assert check(PipelineState()) is False  # no plan yet: not a reason to skip
