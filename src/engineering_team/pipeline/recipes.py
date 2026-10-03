"""Recipes: a run's stage list as data.

A :class:`Recipe` is an ordered list of :class:`StageSpec`. Nothing in it is code: a stage names
its kind, its teammates, the contracts it reads and writes, the conditions that skip it, how
often a failure is retried, and how the controller verifies its output. The pipeline
(``pipeline/flow.py``) interprets it, so a new mode is a new YAML file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.settings import Settings

StageKind = Literal["agent", "controller", "parallel", "verify", "review", "analyze", "reproduce"]
VerificationPolicy = Literal["none", "artifacts"]

# Contracts a stage can read or write; anything else must be ``file:<path>`` (outputs only) or
# ``request`` (inputs only).
CONTRACTS = ("spec", "plan", "triage", "repro", "fix_note")
FILE_PREFIX = "file:"
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

# Named conditions a stage can be skipped on; each looks at the pipeline's state and settings.
CONDITIONS: dict[str, Callable[[PipelineState, Settings], bool]] = {
    "no_work_packages": lambda state, _: state.plan is not None and not state.plan.work_packages,
    # The smoke profile and ``team_profile = "minimal"`` run only the essential stages.
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
        needs_team = ("agent", "parallel", "verify", "review", "analyze", "reproduce")
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
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    stages: list[StageSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> Recipe:
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


def bundled_recipes() -> list[str]:
    root = resources.files("engineering_team") / "modes" / "recipes"
    return sorted(
        entry.name.removesuffix(".yaml") for entry in root.iterdir() if entry.name.endswith(".yaml")
    )


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
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise RecipeError(
            f"Recipe {source} is invalid at {where or 'top level'}: {first['msg']}"
        ) from exc


def load_recipe(name_or_path: str | Path) -> Recipe:
    """A bundled recipe by name (``new``) or a recipe file by path."""

    candidate = Path(name_or_path).expanduser()
    if candidate.suffix in (".yaml", ".yml") or candidate.is_file():
        try:
            return parse_recipe(candidate.read_text(encoding="utf-8"), source=str(candidate))
        except OSError as exc:
            raise RecipeError(f"Cannot read recipe file {candidate}: {exc}") from exc
    name = str(name_or_path)
    available = bundled_recipes()
    if name not in available:
        raise RecipeError(f"Unknown recipe '{name}'. Bundled recipes: {', '.join(available)}.")
    text = (resources.files("engineering_team") / "modes" / "recipes" / f"{name}.yaml").read_text(
        encoding="utf-8"
    )
    return parse_recipe(text, source=f"{name}.yaml")
