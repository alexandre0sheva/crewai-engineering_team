"""Recipes: a run's stage list as data.

A :class:`Recipe` is an ordered list of :class:`StageSpec`. Nothing in it is code: a stage names
its kind, its teammates, the contracts it reads and writes, the conditions that skip it, how
often a failure is retried, and how the controller verifies its output. The pipeline
(``pipeline/flow.py``) interprets it, so a new mode is a new YAML file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from engineering_team.settings import user_config_directory

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.settings import Settings

StageKind = Literal[
    "agent", "controller", "parallel", "verify", "review", "analyze", "reproduce", "upgrade"
]
# Named sets of paths an ``agent`` stage may write (``modes/policies.py`` has what they hold).
WriteScopeName = Literal["tests", "docs", "manifests"]
VerificationPolicy = Literal["none", "artifacts"]

# Contracts a stage can read or write; anything else must be ``file:<path>`` (outputs only) or
# ``request`` (inputs only).
CONTRACTS = ("spec", "plan", "triage", "repro", "fix_note", "upgrades")
FILE_PREFIX = "file:"
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

# Named conditions a stage can be skipped on; each looks at the pipeline's state and settings.
CONDITIONS: dict[str, Callable[[PipelineState, Settings], bool]] = {
    "no_work_packages": lambda state, _: state.plan is not None and not state.plan.work_packages,
    # The smoke profile and ``team_profile = "minimal"`` run only the essential stages.
    # maintain's security audit only changes code when asked to (``--fix``) and has findings.
    "fixes_not_requested": lambda state, _: not state.options.get("fix_findings"),
    "fixes_requested": lambda state, _: bool(state.options.get("fix_findings")),
    "no_findings": lambda state, _: not state.findings and not state.audit_findings,
    # ``review`` found no change to look at (a clean branch, nothing against the base).
    "nothing_to_review": lambda state, _: bool((state.isolation or {}).get("empty")),
    "minimal_team": lambda _, settings: (
        settings.profile == "smoke" or settings.team_profile == "minimal"
    ),
}


class RecipeError(ValueError):
    """A recipe that cannot be loaded or is not valid; the message says what to fix."""


class StageSpec(BaseModel):
    """One stage of a recipe.

    ``agent`` runs one small crew (one teammate, one task); ``parallel`` runs one crew per work
    package of the plan (sequentially for now); ``controller`` runs the controller-side action
    named by ``action`` (no model); ``verify`` is the controller-run verification: it runs the
    project's checks itself and, while required checks fail, hands the failures to its first
    teammate to repair (``budget.max_repair_rounds``). ``retry`` is how many extra attempts a
    failed stage gets.
    ``verification_policy`` ``artifacts`` makes the controller require every ``file:`` output.
    ``review`` runs its teammates side by side as read-only reviewers; the controller merges their
    findings into ``docs/review.md`` and sends serious ones to repair (``review.fail_on``).
    ``analyze`` splits an existing codebase into chunks and has its first teammate's read-only
    analysts study them side by side; the controller writes the codebase map.
    ``reproduce`` (fix mode) has its first teammate write a failing test or script, runs it as the
    controller and accepts it only if it fails for a real reason; ``fix.max_repro_attempts`` is
    its retry budget and when it is spent the run stops with questions (``needs-info``).
    ``upgrade`` (maintain's ``upgrade-deps``) applies the dependency upgrades the previous stage
    planned, grouped, runs the controller's checks after each group and bisects a failing one, so
    what could not be upgraded is reported with the check that said so.
    ``instructions`` is a task written in the recipe itself (the ``custom`` prompt wraps it), for
    a recipe that adds no prompt to ``config/stages.yaml``; ``write_scope`` limits an ``agent``,
    ``verify`` (its repair agent) or ``upgrade`` stage to one named set of paths; ``repair: false``
    keeps a ``review`` stage's findings from being sent to the verify stage's repair agent.
    ``optional`` stages are skipped when none of their teammates is enabled.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: StageKind = "agent"
    teammates: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    skip_if: list[str] = Field(default_factory=list)
    retry: int = Field(default=0, ge=0, le=3)
    verification_policy: VerificationPolicy = "none"
    optional: bool = False
    action: str | None = None
    description: str = ""
    # The task prompt in config/stages.yaml to use instead of the one the stage's kind or name
    # picks (a repository mode's ``implement`` is told to keep the diff small).
    prompt: str | None = None
    # A ``parallel`` stage only: its packages may own the project's shared root files (manifests,
    # README, CI). A project built from nothing keeps those for foundation and integrate; a
    # change to an existing project has no such stage, so its packages need them.
    allow_shared: bool = False
    instructions: str | None = None
    write_scope: WriteScopeName | None = None
    repair: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value):
            raise ValueError(f"stage name {value!r} must be lowercase letters, digits, and _")
        return value

    @property
    def contract_outputs(self) -> list[str]:
        return [name for name in self.outputs if not name.startswith(FILE_PREFIX)]

    @property
    def file_outputs(self) -> list[str]:
        return [
            name.removeprefix(FILE_PREFIX) for name in self.outputs if name.startswith(FILE_PREFIX)
        ]

    @model_validator(mode="after")
    def _consistent(self) -> StageSpec:
        needs_team = ("agent", "parallel", "verify", "review", "analyze", "reproduce", "upgrade")
        if self.kind in needs_team and not self.teammates:
            raise ValueError(f"stage {self.name!r} ({self.kind}) needs at least one teammate")
        if self.kind == "controller" and not self.action:
            raise ValueError(f"controller stage {self.name!r} needs an action")
        if self.kind == "verify" and (self.outputs or self.verification_policy != "none"):
            raise ValueError(
                f"verify stage {self.name!r} writes docs/verification.md itself: it takes no "
                "outputs and no verification_policy"
            )
        if self.kind == "verify" and self.retry:
            raise ValueError(f"verify stage {self.name!r} cannot be retried: it repairs itself")
        if self.kind == "analyze" and (self.outputs or self.verification_policy != "none"):
            raise ValueError(
                f"analyze stage {self.name!r} writes the codebase map itself: it takes no "
                "outputs and no verification_policy"
            )
        if self.kind == "reproduce" and (self.retry or self.outputs != ["repro"]):
            raise ValueError(
                f"reproduce stage {self.name!r} must output exactly 'repro' and takes no retry: "
                "its attempts are fix.max_repro_attempts"
            )
        if self.instructions is not None and (
            self.kind not in ("agent", "parallel") or self.prompt or not self.instructions.strip()
        ):
            raise ValueError(
                f"stage {self.name!r}: instructions go on an agent or parallel stage, are not "
                "empty, and replace prompt (give one or the other)"
            )
        if self.write_scope is not None and self.kind not in ("agent", "verify", "upgrade"):
            raise ValueError(
                f"only agent, verify, and upgrade stages take write_scope (stage {self.name!r})"
            )
        if not self.repair and self.kind != "review":
            raise ValueError(f"only review stages take repair (stage {self.name!r})")
        if self.kind == "upgrade" and (self.outputs or self.verification_policy != "none"):
            raise ValueError(f"upgrade stage {self.name!r} writes its report itself: no outputs")
        if self.allow_shared and self.kind != "parallel":
            raise ValueError(f"only parallel stages take allow_shared (stage {self.name!r})")
        if self.kind != "controller" and self.action:
            raise ValueError(f"only controller stages take an action (stage {self.name!r})")
        for output in self.outputs:
            if output.startswith(FILE_PREFIX):
                if not output.removeprefix(FILE_PREFIX).strip():
                    raise ValueError(f"stage {self.name!r} has an empty file output")
            elif output not in CONTRACTS:
                raise ValueError(
                    f"stage {self.name!r} output {output!r} must be one of {CONTRACTS} "
                    f"or 'file:<path>'"
                )
        for name in self.inputs:
            if name != "request" and name not in CONTRACTS:
                raise ValueError(
                    f"stage {self.name!r} input {name!r} must be 'request' or one of {CONTRACTS}"
                )
        unknown = [name for name in self.skip_if if name not in CONDITIONS]
        if unknown:
            raise ValueError(
                f"stage {self.name!r} has unknown skip_if {unknown}; known: {sorted(CONDITIONS)}"
            )
        if self.verification_policy == "artifacts" and not self.file_outputs:
            raise ValueError(
                f"stage {self.name!r} asks for artifact verification but has no file: outputs"
            )
        return self


class Recipe(BaseModel):
    """An ordered list of stages, and the policies the verify stage enforces on the change
    (``modes/policies.py``: for example ``tests_only`` for a mode that must not touch the code).

    ``source`` (``bundled``, ``user``, or ``project``) and ``path`` say where it was loaded from;
    they are not part of the recipe's content or digest."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    policies: list[str] = Field(default_factory=list)
    stages: list[StageSpec] = Field(min_length=1)
    source: str = Field(default="bundled", exclude=True)
    path: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _ordered(self) -> Recipe:
        from engineering_team.modes.policies import POLICY_NAMES

        unknown = [name for name in self.policies if name not in POLICY_NAMES]
        if unknown:
            raise ValueError(f"unknown policies {unknown}; known: {sorted(POLICY_NAMES)}")
        if self.policies and not any(s.kind == "verify" for s in self.stages):
            raise ValueError("policies are enforced by the verify stage, which this recipe lacks")
        seen: set[str] = set()
        produced: set[str] = {"request"}
        for index, stage in enumerate(self.stages):
            if stage.name in seen:
                raise ValueError(f"duplicate stage name {stage.name!r}")
            seen.add(stage.name)
            if stage.kind == "verify" and any(s.kind == "verify" for s in self.stages[:index]):
                raise ValueError("a recipe can have only one verify stage")
            missing = [name for name in stage.inputs if name not in produced]
            if missing:
                raise ValueError(
                    f"stage {stage.name!r} reads {missing}, which no earlier stage produces"
                )
            produced.update(stage.contract_outputs)
        return self

    def stage(self, name: str) -> StageSpec:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(name)

    @property
    def digest(self) -> str:
        """Identifies the recipe's content: a run only resumes under the recipe it started with."""

        payload = self.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


PROJECT_RECIPES = ".engineering-team/recipes"
RECIPE_NAME = re.compile(r"[a-z][a-z0-9_-]*")
MAX_PROBLEMS = 4


def bundled_recipes() -> list[str]:
    root = resources.files("engineering_team") / "modes" / "recipes"
    return sorted(
        entry.name.removesuffix(".yaml") for entry in root.iterdir() if entry.name.endswith(".yaml")
    )


def user_recipes_dir() -> Path | None:
    """``~/.config/engineering-team/recipes``, or ``None`` when there is no home directory."""

    base = user_config_directory()
    return base / "recipes" if base is not None else None


def recipe_directories(root: Path | None) -> list[tuple[str, Path]]:
    """Where recipes are looked for before the bundled ones, in precedence order: the project's
    ``.engineering-team/recipes`` (``root`` is the project), then the user's."""

    found: list[tuple[str, Path]] = []
    if root is not None:
        found.append(("project", root / PROJECT_RECIPES))
    if (user := user_recipes_dir()) is not None:
        found.append(("user", user))
    return found


def _where(data: object, loc: tuple[int | str, ...]) -> str:
    """``stages[2] ('tests').kind``: a validation error's location with the stage's name."""

    parts: list[str] = []
    stages = data.get("stages") if isinstance(data, dict) else None
    for index, part in enumerate(loc):
        if isinstance(part, int):
            name = ""
            if loc[:index] == ("stages",) and isinstance(stages, list) and part < len(stages):
                item = stages[part]
                name = (
                    f" ({item.get('name')!r})" if isinstance(item, dict) and "name" in item else ""
                )
            parts.append(f"[{part}]{name}")
        else:
            parts.append(("." if parts else "") + part)
    return "".join(parts)


def parse_recipe(text: str, *, source: str) -> Recipe:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecipeError(f"Recipe {source} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeError(f"Recipe {source} must be a YAML mapping with 'name' and 'stages'.")
    try:
        return Recipe.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{_where(data, tuple(e['loc'])) or 'top level'}: {e['msg']}"
            for e in exc.errors()[:MAX_PROBLEMS]
        ]
        raise RecipeError(f"Recipe {source} is invalid at " + "; ".join(problems)) from exc


def read_recipe_file(
    path: Path, *, source: str, teammates: Collection[str] | None = None
) -> Recipe:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RecipeError(f"Cannot read recipe file {path}: {exc}") from exc
    recipe = parse_recipe(text, source=str(path))
    if source != "file" and recipe.name != path.stem:
        raise RecipeError(
            f"Recipe {path} is named {recipe.name!r} inside but {path.stem!r} by its file: "
            "they must match (the run records the name)."
        )
    from engineering_team.pipeline.recipe_check import check_recipe

    problems = check_recipe(recipe, teammates)
    if problems:
        raise RecipeError(f"Recipe {path} cannot run: " + "; ".join(problems[:MAX_PROBLEMS]))
    recipe.source, recipe.path = source, str(path)
    return recipe


def _candidate(directory: Path, name: str) -> Path | None:
    for extension in (".yaml", ".yml"):
        path = directory / f"{name}{extension}"
        if path.is_file():
            return path
    return None


def load_recipe(
    name_or_path: str | Path,
    root: Path | None = None,
    *,
    teammates: Collection[str] | None = None,
) -> Recipe:
    """A recipe by name or by file path.

    A name is looked up in the project's ``.engineering-team/recipes`` (``root`` is the project),
    then the user's ``~/.config/engineering-team/recipes``, then the bundled recipes, so a file
    there can add a recipe or replace a bundled one. A recipe from a file is checked more
    strictly than a bundled one (its actions, prompts, and, with ``teammates``, its team).
    """

    candidate = Path(name_or_path).expanduser()
    if candidate.suffix in (".yaml", ".yml") or candidate.is_file():
        return read_recipe_file(candidate, source="file", teammates=teammates)
    name = str(name_or_path)
    if RECIPE_NAME.fullmatch(name):
        for source, directory in recipe_directories(root):
            if (path := _candidate(directory, name)) is not None:
                return read_recipe_file(path, source=source, teammates=teammates)
    available = bundled_recipes()
    if name not in available:
        raise RecipeError(
            f"Unknown recipe '{name}'. Bundled recipes: {', '.join(available)}; your own go in "
            f"{PROJECT_RECIPES}/ or {user_recipes_dir() or '~/.config/engineering-team/recipes'}."
        )
    text = (resources.files("engineering_team") / "modes" / "recipes" / f"{name}.yaml").read_text(
        encoding="utf-8"
    )
    return parse_recipe(text, source=f"{name}.yaml")


PINNED_RECIPE = "recipe.yaml"


def pin_recipe(run_dir: Path, recipe: Recipe) -> None:
    """Keep a copy of a recipe that came from a file in the run directory: a run resumes under
    the recipe it started with, even from a worktree or copy that does not hold the file."""

    from engineering_team.atomic_io import atomic_write_text

    text = yaml.safe_dump(recipe.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    atomic_write_text(run_dir / PINNED_RECIPE, text)


def pinned_recipe(run_dir: Path) -> Recipe | None:
    """The recipe pinned by :func:`pin_recipe`, or ``None`` when the run used a bundled one."""

    path = run_dir / PINNED_RECIPE
    if not path.is_file():
        return None
    recipe = parse_recipe(path.read_text(encoding="utf-8"), source=str(path))
    recipe.source, recipe.path = "pinned", str(path)
    return recipe
