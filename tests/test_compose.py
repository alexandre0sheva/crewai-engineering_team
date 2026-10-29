"""The recipe composer: golden recipes, mixed intents, the rule file's errors, explanations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pipeline_fakes import REQUEST, settings_for
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import TaskProfile, TaskTarget
from engineering_team.pipeline.compose import choose_recipe, compose, render_plan
from engineering_team.pipeline.compose_rules import ComposeError, bundled_rules_text, parse_rules
from engineering_team.pipeline.recipes import load_recipe, pinned_recipe
from engineering_team.runtime.run_store import RunStore

# Each 0.2.0 mode (and maintenance preset, and `analyze --deep`) and the profile that stands
# for it. Composing the profile must give the bundled recipe.
CANONICAL: dict[str, tuple[list[str], str]] = {
    "new": (["new_project"], "new"),
    "feature": (["feature"], "existing"),
    "fix": (["bugfix"], "existing"),
    "add-tests": (["tests"], "existing"),
    "refactor": (["refactor"], "existing"),
    "upgrade-deps": (["upgrade_deps"], "existing"),
    "docs": (["docs"], "existing"),
    "security-audit": (["security_audit"], "existing"),
    "custom": (["performance"], "existing"),
    "review": (["review"], "existing"),
    "adopt": (["question"], "existing"),
}


def profile(*intents: str, target: str = "existing", **fields: Any) -> TaskProfile:
    return TaskProfile.model_validate(
        {"intents": list(intents), "target": TaskTarget(kind=target), "size": "medium", **fields}
    )


def stages(recipe: Any) -> list[dict[str, Any]]:
    return [stage.model_dump(exclude={"why"}) for stage in recipe.stages]


def names(*intents: str, **fields: Any) -> list[str]:
    return [stage.name for stage in compose(profile(*intents, **fields)).recipe.stages]


# -- golden recipes --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CANONICAL))
def test_every_bundled_recipe_is_composed_from_its_canonical_profile(name: str) -> None:
    intents, target = CANONICAL[name]
    bundled = load_recipe(name)

    composed = compose(profile(*intents, target=target)).recipe

    assert stages(composed) == stages(bundled)
    assert composed.policies == bundled.policies
    assert composed.mode == bundled.mode
    assert all(stage.why for stage in composed.stages)


def test_every_bundled_recipe_has_a_canonical_profile() -> None:
    from engineering_team.pipeline.recipes import bundled_recipes

    assert set(bundled_recipes()) == set(CANONICAL)


def test_the_why_field_does_not_change_a_bundled_recipes_digest() -> None:
    bundled = load_recipe("fix")
    explained = bundled.model_copy(
        update={"stages": [s.model_copy(update={"why": "because"}) for s in bundled.stages]}
    )

    assert bundled.digest != explained.digest
    assert bundled.digest == load_recipe("fix").digest
    assert "why" not in json.dumps([s.model_dump(exclude_defaults=True) for s in bundled.stages])


# -- mixed intents, size and risk -------------------------------------------------------------


def test_a_bugfix_that_also_asks_for_docs_documents_before_verifying() -> None:
    composed = names("bugfix", "docs")

    assert composed == [
        "profile", "baseline", "map", "triage", "reproduce", "fix", "docs", "verify", "review",
        "summary",
    ]  # fmt: skip
    recipe = compose(profile("bugfix", "docs")).recipe
    assert recipe.stage("docs").prompt == "docs_legacy" and recipe.policies == []


def test_a_feature_with_tests_keeps_the_features_own_tests_stage() -> None:
    recipe = compose(profile("feature", "tests")).recipe

    assert [s.name for s in recipe.stages].count("tests") == 1
    assert recipe.stage("tests").inputs == ["spec", "plan"]
    assert recipe.policies == []


def test_a_bugfix_with_tests_adds_characterization_tests() -> None:
    recipe = compose(profile("bugfix", "tests")).recipe

    assert recipe.stage("tests").write_scope == "tests"
    assert recipe.stage("tests").why == "The request also asks for tests."


def test_a_request_not_only_for_tests_drops_the_tests_only_policy() -> None:
    assert compose(profile("tests")).recipe.policies == ["tests_only"]
    assert compose(profile("tests", "docs")).recipe.policies == []
    assert "docs" in names("tests", "docs")


def test_a_small_low_risk_change_has_no_map_but_a_risky_one_does() -> None:
    assert "map" not in names("bugfix", size="small")
    assert "map" not in names("refactor", size="trivial")
    assert "map" in names("bugfix", size="small", risk="medium")
    assert "map" in names("refactor", size="large")


def test_a_new_project_that_asks_for_docs_and_devops_always_runs_them() -> None:
    recipe = compose(profile("new_project", "docs", "devops", target="new")).recipe

    assert recipe.stage("docs").skip_if == ["minimal_team"]
    assert recipe.stage("devops").skip_if == ["minimal_team"]
    assert recipe.mode == "new"


def test_a_question_without_a_project_has_no_recipe_yet() -> None:
    with pytest.raises(ComposeError, match="No stage applies to a question request on a new"):
        compose(profile("question", target="new"))


# -- explanations ----------------------------------------------------------------------------


def test_every_stage_says_why_and_the_preview_shows_it() -> None:
    composition = compose(profile("bugfix", "docs"))
    preview = render_plan(composition)

    assert composition.recipe.description == (
        "Composed for bugfix (also docs) on an existing project: medium, low risk."
    )
    assert composition.recipe.stage("profile").why.startswith("An existing project is profiled")
    assert " 7. docs [agent, session]" in preview
    assert "    Why: The request also asks for documentation." in preview
    assert "map [analyze, sessions] (skipped when small_task)" in preview


def test_a_prerequisite_no_rule_asked_for_says_what_needs_it() -> None:
    text = bundled_rules_text().replace(
        "    add: [target, review_diff, review_gate]", "    add: [review_gate]"
    )
    rules = parse_rules(text, source="edited")

    recipe = compose(profile("review"), rules).recipe

    assert [s.name for s in recipe.stages] == ["profile", "target", "review", "gate"]
    assert recipe.stage("review").why == "Needed by gate."
    assert recipe.stage("target").why == "Needed by review."


# -- the rule file ---------------------------------------------------------------------------


def test_an_invalid_rule_file_says_one_line_per_problem() -> None:
    text = """
library:
  - id: profile
    cost: cheap
    summary: x
    stage: {name: profile, kind: controller, action: repo_profile}
  - id: fix
    cost: session
    summary: x
    requires: [reproduce]
    stage: {name: fix, kind: agent, teammates: [debugger]}
  - id: fix
    cost: session
    summary: x
    stage: {name: Fix, kind: agent}
rules:
  - when: {target: existing, primary: [coffee]}
    add: [profile]
    why: x
  - when: {target: existing}
    add: [nothing]
    policies: [no_such_policy]
    why: x
"""
    with pytest.raises(ComposeError) as error:
        parse_rules(text, source="mine.yaml")

    lines = str(error.value).splitlines()
    assert lines[0] == "mine.yaml is invalid:"
    assert any(line.startswith("  library[0] ('profile').cost:") for line in lines)
    assert any("library[2] ('fix').stage.name" in line for line in lines)
    assert any("rules[0].when.primary[0]: Input should be" in line for line in lines)


def test_cross_references_are_checked_one_line_each() -> None:
    text = """
library:
  - id: fix
    cost: session
    summary: x
    requires: [reproduce]
    stage: {name: fix, kind: agent, teammates: [debugger]}
  - id: fix
    cost: session
    summary: y
    stage: {name: fix, kind: agent, teammates: [debugger]}
rules:
  - when: {target: existing}
    add: [fix, nothing]
    policies: [no_such_policy]
    why: x
"""
    with pytest.raises(ComposeError) as error:
        parse_rules(text, source="mine.yaml")

    assert str(error.value).splitlines()[1:] == [
        "  library[0] ('fix'): requires 'reproduce', which is not an earlier entry",
        "  library[1] ('fix'): the id is used twice",
        "  rules[0]: unknown library entries nothing",
        "  rules[0]: unknown policies no_such_policy",
    ]


def test_a_rule_file_that_is_not_yaml_or_not_a_mapping_is_refused() -> None:
    with pytest.raises(ComposeError, match="not valid YAML"):
        parse_rules("library: [", source="x")
    with pytest.raises(ComposeError, match="must be a mapping"):
        parse_rules("- 1\n", source="x")


# -- choosing a recipe, and the run record ---------------------------------------------------


def test_a_named_recipe_wins_over_composition(tmp_path: Path) -> None:
    own = tmp_path / ".engineering-team" / "recipes"
    own.mkdir(parents=True)
    (own / "mine.yaml").write_text(
        "name: mine\nstages:\n  - {name: profile, kind: controller, action: repo_profile}\n"
    )
    bugfix = profile("bugfix")

    assert choose_recipe(bugfix, "mine", tmp_path).source == "project"
    assert choose_recipe(bugfix, "docs", tmp_path).name == "docs"
    assert choose_recipe(bugfix, None, tmp_path).source == "composed"


def test_a_run_with_a_composed_recipe_records_why_and_pins_it(tmp_path: Path) -> None:
    from engineering_team import main

    recipe = compose(profile("bugfix", "docs")).recipe
    prepared = main._open_run(
        settings_for(tmp_path), mode="build", requirements=REQUEST, recipe=recipe
    )
    try:
        manifest = RunStore(prepared.ctx.workspace.root).load(prepared.ctx.run_id)
        pinned = pinned_recipe(prepared.ctx.run_dir)
    finally:
        prepared.lock.release()

    assert manifest.recipe == recipe.name
    assert manifest.stage_reasons["docs"] == "The request also asks for documentation."
    assert set(manifest.stage_reasons) == {s.name for s in recipe.stages}
    assert pinned is not None and pinned.digest == recipe.digest


# -- the preview command ---------------------------------------------------------------------


def test_recipes_compose_previews_the_plan() -> None:
    runner = CliRunner()

    human = runner.invoke(app, ["recipes", "compose", "--intent", "bugfix", "--intent", "docs"])
    data = runner.invoke(
        app, ["--json", "recipes", "compose", "--intent", "refactor", "--size", "small"]
    )
    bad = runner.invoke(app, ["recipes", "compose", "--intent", "coffee"])

    assert human.exit_code == 0 and "Why: The request also asks for documentation." in human.stdout
    doc = json.loads(data.stdout)
    assert doc["policies"] == ["tests_untouched", "manifests_untouched", "diff_size"]
    assert [s["name"] for s in doc["stages"]][:3] == ["profile", "baseline", "precondition"]
    assert all(s["why"] and s["cost"] and s["summary"] for s in doc["stages"])
    assert bad.exit_code == 2
