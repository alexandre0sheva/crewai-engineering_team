"""``recipes list``: every recipe name available from a project and the user's directory."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from engineering_team.pipeline.recipes import (
    RECIPE_NAME,
    Recipe,
    RecipeError,
    bundled_recipes,
    load_recipe,
    read_recipe_file,
    recipe_directories,
)


@dataclass(frozen=True)
class RecipeInfo:
    """One recipe name as ``recipes list`` shows it: the one that wins, and what it hides."""

    name: str
    source: str  # bundled, user, or project
    path: str | None
    recipe: Recipe | None
    error: str | None = None
    hides: tuple[str, ...] = ()  # sources of the same name that lose to this one


def list_recipes(
    root: Path | None = None, *, teammates: Collection[str] | None = None
) -> list[RecipeInfo]:
    """Every recipe name available from ``root`` and the user's directory, with the winner's
    source. A file that cannot be loaded is listed with its error rather than hidden."""

    sources: dict[str, list[tuple[str, Path | None]]] = {}
    for source, directory in recipe_directories(root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix in (".yaml", ".yml") and RECIPE_NAME.fullmatch(path.stem):
                sources.setdefault(path.stem, []).append((source, path))
    for name in bundled_recipes():
        sources.setdefault(name, []).append(("bundled", None))
    found: list[RecipeInfo] = []
    for name in sorted(sources):
        (source, where), *others = sources[name]
        hides = tuple(other for other, _ in others)
        try:
            recipe = (
                read_recipe_file(where, source=source, teammates=teammates)
                if where is not None
                else load_recipe(name)
            )
            found.append(
                RecipeInfo(name, source, str(where) if where else None, recipe, None, hides)
            )
        except RecipeError as exc:
            found.append(
                RecipeInfo(name, source, str(where) if where else None, None, str(exc), hides)
            )
    return found
