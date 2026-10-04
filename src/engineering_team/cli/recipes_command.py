"""``recipes list`` and ``recipes show NAME``: which recipes exist and what each one does.

A recipe is a YAML file (``modes/recipes/`` for the bundled ones); yours go in the project's
``.engineering-team/recipes/`` or ``~/.config/engineering-team/recipes/`` and win over a bundled
recipe of the same name. A file that is wrong is listed with its error, not hidden.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from engineering_team.cli.context import fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.pipeline.recipe_list import RecipeInfo, list_recipes
from engineering_team.pipeline.recipes import PROJECT_RECIPES, Recipe, StageSpec, user_recipes_dir
from engineering_team.team import TeamError, build_roster

recipes_app = typer.Typer(
    help="The recipes: list them, and see what each stage of one does (docs/USAGE.md).",
    no_args_is_help=True,
)
RepoOpt = Annotated[
    str, typer.Option(help="The project whose own recipes are included (default: here).")
]


def _roster_keys(g: Any, config: str | None) -> set[str]:
    try:
        return set(build_roster(load(g, config)).members)
    except TeamError as exc:
        fail(str(exc))


def stage_doc(stage: StageSpec) -> dict[str, Any]:
    """One stage as plain data, with only what it sets."""

    data = stage.model_dump(mode="json", exclude_defaults=True)
    data["name"], data["kind"] = stage.name, stage.kind
    return data


def recipe_doc(info: RecipeInfo) -> dict[str, Any]:
    recipe = info.recipe
    return {
        "name": info.name,
        "source": info.source,
        "path": info.path,
        "hides": list(info.hides),
        "error": info.error,
        "description": recipe.description if recipe else "",
        "policies": recipe.policies if recipe else [],
        "stages": [stage_doc(s) for s in recipe.stages] if recipe else [],
    }


def _what(stage: StageSpec) -> str:
    if stage.kind == "controller":
        return f"action {stage.action}"
    if stage.instructions is not None:
        return "instructions: " + " ".join(stage.instructions.split())[:60]
    return f"prompt {stage.prompt}" if stage.prompt else ""


@recipes_app.command("list")
def list_(ctx: typer.Context, repo: RepoOpt = ".", config: ConfigOpt = None) -> None:
    """Every recipe available here: bundled, yours, and which one wins."""

    g = get_globals(ctx)
    root = Path(repo).expanduser().resolve()
    infos = list_recipes(root, teammates=_roster_keys(g, config))
    if g.json:
        print_json([recipe_doc(i) for i in infos])
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for column in ("Name", "Source", "Stages", "Description"):
        table.add_column(column)
    for info in infos:
        stages = str(len(info.recipe.stages)) if info.recipe else "-"
        description = info.recipe.description if info.recipe else f"INVALID: {info.error}"
        hides = f" (hides {', '.join(info.hides)})" if info.hides else ""
        table.add_row(info.name, info.source + hides, stages, description)
    console = g.console()
    console.print(table)
    console.print(
        f"Your own recipes go in {root / PROJECT_RECIPES} or "
        f"{user_recipes_dir() or '~/.config/engineering-team/recipes'}.",
        markup=False,
        highlight=False,
        soft_wrap=True,
    )


@recipes_app.command("show")
def show(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="A recipe from `recipes list`.")],
    repo: RepoOpt = ".",
    config: ConfigOpt = None,
) -> None:
    """The stages of one recipe: who works them, what they read and write, when they are skipped."""

    g = get_globals(ctx)
    root = Path(repo).expanduser().resolve()
    infos = {i.name: i for i in list_recipes(root, teammates=_roster_keys(g, config))}
    info = infos.get(name)
    if info is None:
        fail(f"Unknown recipe {name!r}. Known: {', '.join(infos)}.")
    if g.json:
        print_json(recipe_doc(info))
        return
    console = g.console()
    if info.recipe is None:
        fail(f"Recipe {name!r} ({info.path}) is invalid: {info.error}")
    recipe: Recipe = info.recipe
    head = f"{recipe.name} ({info.source}{', ' + info.path if info.path else ''})"
    console.print(head, markup=False, highlight=False, soft_wrap=True)
    console.print(recipe.description, markup=False, highlight=False, soft_wrap=True)
    if recipe.policies:
        console.print(f"Policies: {', '.join(recipe.policies)}", markup=False, soft_wrap=True)
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for column in ("Stage", "Kind", "Teammates", "What", "Skipped when"):
        table.add_column(column)
    for stage in recipe.stages:
        extras = [f"scope {stage.write_scope}"] if stage.write_scope else []
        extras += ["optional"] if stage.optional else []
        what = "; ".join(x for x in (_what(stage), *extras) if x)
        table.add_row(
            stage.name, stage.kind, ", ".join(stage.teammates), what, ", ".join(stage.skip_if)
        )
    console.print(table)
