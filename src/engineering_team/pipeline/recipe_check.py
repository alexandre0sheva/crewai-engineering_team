"""Checks on a recipe that its shape does not show (``pipeline/recipes.py`` checks the shape)."""

from __future__ import annotations

from collections.abc import Collection

from engineering_team.pipeline.recipes import Recipe

MAX_LISTED = 12


def check_recipe(recipe: Recipe, teammates: Collection[str] | None = None) -> list[str]:
    """What is wrong with ``recipe`` that its shape does not show: a controller action nobody
    registered, a prompt that does not exist, a teammate who is not on the team (when
    ``teammates`` is given). Empty when the recipe can run."""

    from engineering_team.pipeline import executor  # noqa: F401  (registers the actions)
    from engineering_team.pipeline.actions import CONTROLLER_ACTIONS
    from engineering_team.pipeline.stages import prompt_key, prompts

    known_prompts = prompts()
    problems: list[str] = []
    for stage in recipe.stages:
        if stage.kind == "controller" and stage.action not in CONTROLLER_ACTIONS:
            problems.append(
                f"stage {stage.name!r}: unknown action {stage.action!r}; known: "
                f"{', '.join(sorted(CONTROLLER_ACTIONS))}"
            )
        elif stage.kind != "controller" and prompt_key(stage) not in known_prompts:
            problems.append(
                f"stage {stage.name!r}: there is no prompt {prompt_key(stage)!r}; set `prompt:` "
                f"to one of {', '.join(sorted(known_prompts))}, or write the task as "
                "`instructions:`"
            )
        if teammates is not None:
            missing = [key for key in stage.teammates if key not in teammates]
            if missing:
                problems.append(
                    f"stage {stage.name!r}: unknown teammate(s) {', '.join(missing)}; the team is "
                    f"{', '.join(sorted(teammates))}"
                )
    return problems
