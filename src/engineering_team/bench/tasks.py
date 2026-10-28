"""Benchmark tasks: the ``task.yaml`` schema and how a suite directory is loaded.

A task lives in ``<suite>/tasks/<id>/``::

    task.yaml        what the task is and which acceptance criteria judge it
    request.md       what the team is asked (the only text it sees)
    acceptance/      hidden checks (``checks.py``): run by the harness, outside the workspace
    fixture/         brownfield only: the project the team starts from
    change/          review only: files committed over the fixture on a branch to review
    reference/       a known-good solution: written over the fixture (or an empty workspace)

``task.yaml`` also names a ``sabotage``: edits that break the reference on purpose, and the
criteria that must then fail. It proves the checks tell a good solution from a bad one.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
MAINTAIN_PRESETS = ("add-tests", "refactor", "upgrade-deps", "docs", "security-audit", "custom")
# Never copied from a workspace, fixture, or reference: caches and the controller's state.
SKIP_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".git", "node_modules"}
)
CONTROLLER_NAMES = frozenset({".engineering-team"})

Kind = Literal["greenfield", "brownfield"]
Mode = Literal["new", "feature", "fix", "maintain", "review"]
Split = Literal["dev", "heldout"]
Size = Literal["trivial", "small", "medium", "large"]
Difficulty = Literal["easy", "medium", "hard"]
Toolchain = Literal["node", "go"]
# What kind of work a task is (the triage intents of docs/IMPLEMENTATION_PLAN_0.3.0.md, task 10).
Intent = Literal[
    "question", "research", "review", "bugfix", "feature", "new_project", "refactor", "tests",
    "docs", "upgrade_deps", "security_audit", "performance", "devops", "plan_only",
]  # fmt: skip
INTENTS: tuple[str, ...] = get_args(Intent)
# The intent of a 0.2.0 task.yaml that names none: from its mode (and maintain recipe).
MODE_INTENTS = {"new": "new_project", "feature": "feature", "fix": "bugfix", "review": "review"}
RECIPE_INTENTS = {
    "add-tests": "tests", "refactor": "refactor", "upgrade-deps": "upgrade_deps", "docs": "docs",
    "security-audit": "security_audit", "custom": "feature",
}  # fmt: skip


class BenchError(ValueError):
    """A suite or task that cannot be used; the message names the file and says how to fix it."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Criterion(_Strict):
    """One behaviour the hidden checks verify: ``checks.py`` has ``check_<id>`` for it."""

    id: str
    text: str
    required: bool = True

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG.fullmatch(value):
            raise ValueError(f"criterion id {value!r} must match {SLUG.pattern}")
        return value


class Edit(_Strict):
    """Replace ``old`` with ``new`` in ``path`` (exactly once)."""

    path: str
    old: str
    new: str


class Sabotage(_Strict):
    edits: list[Edit] = Field(min_length=1)
    fails: list[str] = Field(min_length=1)  # criteria that must fail once the edits are applied


class BenchTask(_Strict):
    id: str
    title: str
    kind: Kind
    mode: Mode  # the command a live run uses
    intent: Intent  # what kind of work it is (derived from ``mode`` when a task omits it)
    split: Split = "dev"  # "heldout" tasks are never used to tune defaults (docs/BENCHMARKS.md)
    size: Size = "small"
    difficulty: Difficulty = "medium"
    languages: list[str] = Field(default_factory=lambda: ["python"], min_length=1)
    toolchains: list[Toolchain] = []  # what the checks need besides Python (run in Docker)
    timeout_seconds: int = Field(default=1800, ge=60)
    scale: float = Field(default=1.0, gt=0)  # relative size, for the ``--dry-run`` estimate
    request: str = "request.md"
    trace_file: str | None = None  # fix mode: a stack trace file in the task directory
    repro: str | None = None  # fix mode: a command that shows the bug
    maintain_task: str | None = None  # maintain mode: the recipe (``--task``)
    criteria: list[Criterion] = Field(min_length=1)
    sabotage: Sabotage
    root: Path = Field(default=Path("."), exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data: Any) -> Any:
        """Read a 0.2.0 ``task.yaml``: ``subset`` is ``split``, ``language`` is ``languages``,
        and a missing ``intent`` follows from the mode."""

        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "subset" in data and "split" not in data:
            data["split"] = data.pop("subset")
        if isinstance(data.get("language"), str) and "languages" not in data:
            data["languages"] = [data.pop("language")]
        if "intent" not in data:
            mode = data.get("mode")
            recipe = data.get("maintain_task")
            data["intent"] = (
                RECIPE_INTENTS.get(str(recipe), "feature")
                if mode == "maintain"
                else MODE_INTENTS.get(str(mode), "feature")
            )
        return data

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG.fullmatch(value):
            raise ValueError(f"id {value!r} must match {SLUG.pattern}")
        return value

    @property
    def fixture_dir(self) -> Path:
        return self.root / "fixture"

    @property
    def change_dir(self) -> Path:
        """Review tasks: what is committed over the fixture on the branch the team reviews."""

        return self.root / "change"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    @property
    def checks_file(self) -> Path:
        return self.root / "acceptance" / "checks.py"

    @property
    def request_path(self) -> Path:
        return self.root / self.request

    def applies_to(self, strategy: str) -> bool:
        """Only a new project can be built with any strategy; the repository modes need the
        ``pipeline`` (``cli/repo_mode.py`` forces it)."""

        return self.mode == "new" or strategy == "pipeline"

    def validate_files(self) -> None:
        """Check the task directory holds what ``task.yaml`` promises."""

        problems: list[str] = []
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            problems.append("criterion ids must be unique")
        if (self.kind == "greenfield") != (self.mode == "new"):
            problems.append("greenfield tasks use mode 'new'; brownfield tasks the other modes")
        if self.kind == "brownfield" and not self.fixture_dir.is_dir():
            problems.append("a brownfield task needs a fixture/ directory")
        if self.mode == "fix" and not (self.trace_file or self.repro):
            problems.append("a fix task needs trace_file or repro")
        if self.mode == "maintain" and self.maintain_task not in MAINTAIN_PRESETS:
            problems.append(f"maintain_task must be one of {', '.join(MAINTAIN_PRESETS)}")
        if self.mode == "review" and not self.change_dir.is_dir():
            problems.append("a review task needs a change/ directory (the branch to review)")
        for name in (self.request, self.trace_file):
            if name and not (self.root / name).is_file():
                problems.append(f"missing file {name}")
        if not self.checks_file.is_file():
            problems.append("missing acceptance/checks.py")
        if not any(self.reference_dir.rglob("*")):
            problems.append("reference/ is empty")
        for criterion in self.sabotage.fails:
            if criterion not in ids:
                problems.append(f"sabotage.fails names an unknown criterion {criterion!r}")
        if problems:
            raise BenchError(f"Task {self.id} ({self.root / 'task.yaml'}): " + "; ".join(problems))


def load_task(directory: Path) -> BenchTask:
    path = directory / "task.yaml"
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BenchError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchError(f"{path} must be a mapping.")
    try:
        task = BenchTask.model_validate({**data, "root": directory})
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise BenchError(f"{path}: {details}") from exc
    if task.id != directory.name:
        raise BenchError(
            f"{path}: id {task.id!r} must equal the directory name {directory.name!r}."
        )
    task.validate_files()
    return task


def load_suite(suite: Path) -> list[BenchTask]:
    """Every task under ``<suite>/tasks``, sorted by id."""

    tasks_dir = suite / "tasks"
    if not tasks_dir.is_dir():
        raise BenchError(
            f"No benchmark suite at {suite} (expected {tasks_dir}). The suite ships in the "
            "source checkout, not the wheel: run from the repository root or pass --suite DIR."
        )
    found = [load_task(d) for d in sorted(tasks_dir.iterdir()) if (d / "task.yaml").is_file()]
    if not found:
        raise BenchError(f"{tasks_dir} contains no tasks.")
    return found


def copy_tree(source: Path, destination: Path) -> list[str]:
    """Copy ``source`` to ``destination`` (created), skipping caches; returns relative paths."""

    copied: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIP_NAMES or part in CONTROLLER_NAMES for part in relative.parts):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append(relative.as_posix())
    return copied
