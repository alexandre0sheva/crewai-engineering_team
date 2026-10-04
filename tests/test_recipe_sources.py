"""User recipes: where they live, which one wins, and what an invalid one says."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.pipeline.recipe_list import list_recipes
from engineering_team.pipeline.recipes import (
    PROJECT_RECIPES,
    RecipeError,
    bundled_recipes,
    load_recipe,
    parse_recipe,
)

GOOD = """\
name: {name}
description: A recipe of my own.
stages:
  - name: profile
    kind: controller
    action: repo_profile
  - name: tidy
    kind: agent
    teammates: [backend_engineer]
    instructions: Remove the unused imports from the package.
    write_scope: tests
  - name: verify
    kind: verify
    teammates: [debugger]
"""


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    place = tmp_path / "home"
    monkeypatch.setenv("HOME", str(place))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(place / "xdg"))
    return place / "xdg" / "engineering-team" / "recipes"


def write(directory: Path, name: str, text: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(GOOD.format(name=name) if text is None else text, encoding="utf-8")
    return path


def test_a_recipe_file_in_the_project_is_found_by_name(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    write(project / PROJECT_RECIPES, "tidy-imports")

    recipe = load_recipe("tidy-imports", project)

    assert recipe.name == "tidy-imports" and recipe.source == "project"
    assert recipe.path == str(project / PROJECT_RECIPES / "tidy-imports.yaml")
    assert recipe.stage("tidy").instructions.startswith("Remove the unused")  # type: ignore[union-attr]
    assert recipe.stage("tidy").write_scope == "tests"


def test_a_recipe_in_the_users_config_directory_is_found_without_a_project(home: Path) -> None:
    write(home, "mine")

    recipe = load_recipe("mine")

    assert recipe.source == "user"


def test_the_project_wins_over_the_user_and_the_user_over_the_bundled_recipes(
    tmp_path: Path, home: Path
) -> None:
    project = tmp_path / "proj"
    write(project / PROJECT_RECIPES, "fix")
    write(home, "fix")
    write(home, "feature")

    assert load_recipe("fix", project).source == "project"
    assert load_recipe("feature", project).source == "user"
    assert load_recipe("fix").source == "user"
    assert load_recipe("new", project).source == "bundled"


def test_the_listing_shows_the_winner_and_what_it_hides(tmp_path: Path, home: Path) -> None:
    project = tmp_path / "proj"
    write(project / PROJECT_RECIPES, "fix")
    write(home, "extra")

    found = {info.name: info for info in list_recipes(project)}

    assert found["fix"].source == "project" and found["fix"].hides == ("bundled",)
    assert found["extra"].source == "user" and found["extra"].hides == ()
    assert found["feature"].source == "bundled" and found["feature"].path is None
    assert set(bundled_recipes()) <= set(found)


def test_a_broken_file_is_listed_with_its_error_and_does_not_hide_the_rest(
    tmp_path: Path, home: Path
) -> None:
    write(home, "broken", "name: broken\nstages: [{name: x, kind: nonsense}]\n")

    found = {info.name: info for info in list_recipes(tmp_path)}

    assert found["broken"].recipe is None and "broken.yaml" in (found["broken"].error or "")
    assert found["new"].recipe is not None


@pytest.mark.parametrize(
    ("text", "says"),
    [
        (
            "name: x\nstages: [{name: a, kind: agent, teammates: [debugger], prompt: nope}]\n",
            "there is no prompt 'nope'",
        ),
        (
            "name: x\nstages: [{name: a, kind: controller, action: do_magic}]\n",
            "unknown action 'do_magic'",
        ),
        (
            "name: x\nstages:\n  - {name: a, kind: controller, action: repo_profile}\n"
            "  - {name: b, kind: wizard}\n",
            "stages[1] ('b').kind",
        ),
        (
            "name: other\nstages: [{name: a, kind: controller, action: repo_profile}]\n",
            "named 'other' inside but 'x'",
        ),
        (
            "name: x\npolicies: [fast_and_loose]\nstages: "
            "[{name: a, kind: controller, action: repo_profile}]\n",
            "unknown policies",
        ),
        (
            "name: x\npolicies: [tests_only]\nstages: "
            "[{name: a, kind: controller, action: repo_profile}]\n",
            "which this recipe lacks",
        ),
        (
            "name: x\nstages: [{name: a, kind: agent, teammates: [debugger], instructions: ''}]\n",
            "instructions",
        ),
        (
            "name: x\nstages: [{name: a, kind: agent, teammates: [debugger], instructions: do it,"
            " prompt: fix}]\n",
            "replace prompt",
        ),
        (
            "name: x\nstages: [{name: a, kind: controller, action: repo_profile,"
            " write_scope: docs}]\n",
            "only agent, verify, and upgrade stages take write_scope",
        ),
        ("name: x\nstages: [{name: a, kind: agent, teammates: [debugger], bogus: 1}]\n", "bogus"),
        (
            "name: x\nstages: [{name: Bad Name, kind: controller, action: repo_profile}]\n",
            "lowercase",
        ),
    ],
)
def test_an_invalid_recipe_says_where_and_what_to_fix(text: str, says: str, home: Path) -> None:
    path = write(home, "x", text)

    with pytest.raises(RecipeError) as raised:
        load_recipe("x")

    assert str(path) in str(raised.value) and says in str(raised.value)


def test_an_unknown_teammate_is_reported_with_the_team(home: Path) -> None:
    write(home, "mine")

    with pytest.raises(RecipeError) as raised:
        load_recipe("mine", teammates={"debugger"})

    assert "unknown teammate(s) backend_engineer" in str(raised.value)
    assert load_recipe("mine", teammates={"backend_engineer", "debugger"}).name == "mine"


def test_a_name_that_could_escape_the_recipe_directories_is_just_unknown(
    tmp_path: Path, home: Path
) -> None:
    write(tmp_path, "secret")

    with pytest.raises(RecipeError, match="Unknown recipe"):
        load_recipe("../../secret", tmp_path / "proj")


def test_a_recipe_file_given_by_path_is_checked_but_may_be_named_anything(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "whatever", GOOD.format(name="other-name"))

    recipe = load_recipe(path)

    assert recipe.name == "other-name" and recipe.source == "file"


def test_where_a_recipe_came_from_is_not_part_of_its_digest(home: Path) -> None:
    write(home, "mine")
    from_user = load_recipe("mine")
    plain = parse_recipe(GOOD.format(name="mine"), source="x")

    assert from_user.digest == plain.digest and "source" not in from_user.model_dump()


def test_every_bundled_recipe_passes_the_strict_checks() -> None:
    from engineering_team.pipeline.recipe_check import check_recipe

    for name in bundled_recipes():
        recipe = load_recipe(name)
        assert recipe.name == name, name
        assert check_recipe(recipe) == [], name
