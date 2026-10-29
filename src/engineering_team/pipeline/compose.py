"""Composing a recipe from a task profile (``modes/compose.yaml``; docs/ARCHITECTURE.md).

The rules are read top to bottom; each one that holds for the profile adds its library entries,
and the first entry to claim a stage name keeps it (a specific variant before a general one).
An entry's ``requires`` are added after the rules, when no rule added a stage of that name. The
stages are then put in the library's order, which is an order of prerequisites, and the result
is validated like any recipe: its shape
(``Recipe``), and its actions and prompts (``check_recipe``). Each stage records why it is there
(``StageSpec.why``); the run's manifest keeps those reasons and ``recipes compose`` shows them.

A recipe the user names explicitly always wins: :func:`choose_recipe` composes only when no
recipe is named.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering_team.contracts import TaskProfile
from engineering_team.pipeline.compose_rules import ComposeError, ComposeRules, load_rules
from engineering_team.pipeline.recipes import Recipe, load_recipe

COMPOSED_SOURCE = "composed"
# The 0.2.0 mode a composed recipe stands for (a teammate's ``modes`` filter is matched against
# it); review and questions have none, like the bundled ``review`` and ``adopt`` recipes.
MODE_OF: dict[str, str | None] = {
    "bugfix": "fix", "feature": "feature", "new_project": "new", "review": None,
    "question": None, "research": None, "plan_only": None,
}  # fmt: skip


@dataclass(frozen=True)
class ComposedStage:
    """One stage of a composed recipe, for the preview: the library entry it came from."""

    name: str
    entry: str
    cost: str
    summary: str
    why: str


@dataclass(frozen=True)
class Composition:
    recipe: Recipe
    stages: tuple[ComposedStage, ...]


def compose(
    profile: TaskProfile, rules: ComposeRules | None = None, *, teammates: set[str] | None = None
) -> Composition:
    """The recipe for ``profile``. Raises :class:`ComposeError` when no rule gives it a stage or
    the result is not a valid recipe (``teammates``: also check every teammate is on the team)."""

    rules = rules or load_rules()
    claimed: dict[str, tuple[str, str]] = {}  # stage name -> (entry id, why)
    policies: list[str] = []

    def claim(entry_id: str, why: str) -> None:
        claimed.setdefault(rules.entry(entry_id).stage.name, (entry_id, why))

    for rule in rules.rules:
        if rule.when.holds(profile):
            for entry_id in rule.add:
                claim(entry_id, rule.why)
            policies += [name for name in rule.policies if name not in policies]
    # Prerequisites last, so a stage a rule asked for keeps that rule's reason.
    pending = list(claimed.values())
    while pending:
        entry = rules.entry(pending.pop(0)[0])
        for needed in entry.requires:
            name = rules.entry(needed).stage.name
            if name not in claimed:
                claimed[name] = (needed, f"Needed by {entry.stage.name}.")
                pending.append(claimed[name])
    if not claimed:
        raise ComposeError(
            f"No stage applies to a {profile.primary} request on a {profile.target.kind} "
            "project yet (questions, research and plans without a project come with the answer "
            "path)."
        )
    order = sorted(claimed.values(), key=lambda item: rules.position(item[0]))
    stages = tuple(
        ComposedStage(
            name=rules.entry(entry_id).stage.name,
            entry=entry_id,
            cost=rules.entry(entry_id).cost,
            summary=rules.entry(entry_id).summary,
            why=why,
        )
        for entry_id, why in order
    )
    recipe = _recipe(profile, rules, order, policies, teammates)
    return Composition(recipe, stages)


def _recipe(
    profile: TaskProfile,
    rules: ComposeRules,
    order: list[tuple[str, str]],
    policies: list[str],
    teammates: set[str] | None,
) -> Recipe:
    from engineering_team.pipeline.recipe_check import check_recipe

    mode = "new" if profile.target.kind == "new" else MODE_OF.get(profile.primary, "maintain")
    try:
        recipe = Recipe(
            name="composed-" + "-".join(i.replace("_", "-") for i in profile.intents),
            description=describe(profile),
            mode=mode,
            policies=policies,
            stages=[
                rules.entry(entry_id).stage.model_copy(update={"why": why})
                for entry_id, why in order
            ],
        )
    except ValueError as exc:
        raise ComposeError(f"The composed recipe is invalid: {exc}") from exc
    problems = check_recipe(recipe, teammates)
    if problems:
        raise ComposeError("The composed recipe cannot run: " + "; ".join(problems))
    recipe.source = COMPOSED_SOURCE
    return recipe


def describe(profile: TaskProfile) -> str:
    secondary = f" (also {', '.join(profile.intents[1:])})" if profile.intents[1:] else ""
    target = "an existing project" if profile.target.kind == "existing" else "a new project"
    return (
        f"Composed for {profile.primary}{secondary} on {target}: {profile.size}, "
        f"{profile.risk} risk."
    )


def choose_recipe(
    profile: TaskProfile,
    named: str | None = None,
    root: Path | None = None,
    *,
    teammates: set[str] | None = None,
) -> Recipe:
    """The recipe a run uses: the one the user named (their own recipes win over the bundled
    ones, as for ``--task``), otherwise the one composed for ``profile``."""

    if named:
        return load_recipe(named, root, teammates=teammates)
    return compose(profile, teammates=teammates).recipe


def render_plan(composition: Composition) -> str:
    """The plan preview: each stage, what it costs, and why it is there."""

    recipe = composition.recipe
    lines = [recipe.description]
    if recipe.policies:
        lines.append(f"Policies: {', '.join(recipe.policies)}")
    for index, stage in enumerate(composition.stages, 1):
        spec = recipe.stages[index - 1]
        skip = f" (skipped when {', '.join(spec.skip_if)})" if spec.skip_if else ""
        lines.append(f"{index:2}. {stage.name} [{spec.kind}, {stage.cost}]{skip}")
        lines.append(f"    {stage.summary}")
        lines.append(f"    Why: {stage.why}")
    return "\n".join(lines)
