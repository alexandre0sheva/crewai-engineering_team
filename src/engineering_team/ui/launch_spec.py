"""What a start of a run is made of: the request the API accepts (``StartRun``), its options and
their budget, uploads, the record kept per start, and how options become settings overrides."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["new", "feature", "fix", "maintain", "review", "do", "continue"]
REPO_MODES = ("feature", "fix", "maintain", "review")
# ``do`` and ``continue`` take their intent from the request (``--mode`` forces one); the project
# is the ``repo``, or a new one in the workspace root.
ENTRY_MODES = ("do", "continue")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
REQUEST_SUFFIXES = (".md", ".markdown", ".txt", ".rst")
CONTEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv", ".toml")


class StartError(ValueError):
    """A start that cannot happen; ``status`` is the HTTP status that describes why."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_seconds: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)


class RunOptions(BaseModel):
    """The options every start (and a resume) may carry; each is what the CLI option of the same
    name does, or the setting of the same name."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["auto", "pipeline", "single", "team"] | None = None
    attempts: int | None = Field(default=None, ge=1, le=8)  # best-of-N (do, continue)
    profile: str | None = None
    provider: str | None = None
    sandbox: Literal["local", "docker"] | None = None
    allow_web: bool = False
    budget: Budget | None = None
    max_parallel_agents: int | None = Field(default=None, ge=1, le=16)
    team_profile: Literal["full", "minimal"] | None = None  # minimal: no review, DevOps, docs
    disabled_teammates: list[str] = Field(default_factory=list)  # roster toggles


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "new"
    intent: str | None = None  # do: force the kind of work (``do --mode``) instead of triaging
    parent: str | None = None  # continue: the run to follow up
    implement: bool = False  # continue: carry out the plan a plan-only run stored
    request: str | None = Field(default=None, max_length=200_000)  # text; or upload files
    example: str | None = None
    repo: str | None = None  # the project directory (feature, fix, maintain, review)
    project_name: str | None = None
    # Mode-specific.
    task: str | None = None  # maintain
    goal: str | None = None  # maintain
    fix_findings: bool = False  # maintain security-audit: also fix what it finds
    base: str | None = None  # review
    focus: str | None = None  # review
    repro: str | None = None  # fix
    trace: str | None = Field(default=None, max_length=200_000)  # fix: a stack trace or log
    allow_unreproduced: bool = False  # fix
    worktree: bool = False  # isolation (feature, fix, maintain)
    allow_dirty: bool = False
    squash: bool = False
    no_git: bool = False  # new
    interactive: bool = True  # the team may ask questions, answered through the API
    options: RunOptions = Field(default_factory=RunOptions)


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes


class StartRecord(BaseModel):
    run_id: str
    pid: int
    mode: str
    resume: bool = False
    log: str
    started: float
    argv: list[str]
    overrides: dict[str, Any] = Field(default_factory=dict)  # the settings this start changed


def overrides_for(options: RunOptions) -> dict[str, Any]:
    """The dotted settings the options set (the value of ``ENGINEERING_OVERRIDES``)."""

    found: dict[str, Any] = {}
    if options.budget is not None:
        for key, value in options.budget.model_dump().items():
            if value is not None:
                found[f"budget.{key}"] = value
    if options.max_parallel_agents is not None:
        found["parallel.max_parallel_agents"] = options.max_parallel_agents
    if options.team_profile is not None:
        found["team_profile"] = options.team_profile
    for key in options.disabled_teammates:
        found[f"team.{key}.enabled"] = False
    return found


def safe_name(name: str, suffixes: Sequence[str]) -> str:
    """A file name that is only itself: no directories, no odd characters, a known suffix."""

    base = SAFE_NAME.sub("-", Path(name.replace("\\", "/")).name).strip(".-")
    if not base or not base.lower().endswith(tuple(suffixes)):
        raise StartError(f"{name!r} is not an accepted file; use one of: {', '.join(suffixes)}.")
    return base[:100]
