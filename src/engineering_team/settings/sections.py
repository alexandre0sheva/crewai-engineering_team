"""The sections of the configuration: one typed model per ``[table]``."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from engineering_team.extensions.config import (
    EmbedderSettings,
)
from engineering_team.model_routing import (
    TIER_NAMES,
)
from engineering_team.settings.common import Effort

# --- schema --------------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelOverride(_Frozen):
    """Overrides for a tier, a profile slot, or one role. Unset fields change nothing."""

    model: str | None = None
    reasoning_effort: Effort | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_iter: int | None = Field(default=None, ge=1)
    context_window: int | None = Field(default=None, ge=1024)
    api: Literal["completions", "responses"] | None = None

    @field_validator("model")
    @classmethod
    def _provider_qualified(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+/\S+", value):
            raise ValueError("must look like 'provider/model-id' (for example 'openai/gpt-6-luna')")
        return value


class ModelsSettings(_Frozen):
    tiers: dict[str, ModelOverride] = {}
    roles: dict[str, ModelOverride] = {}

    @field_validator("tiers")
    @classmethod
    def _known_tiers(cls, value: dict[str, ModelOverride]) -> dict[str, ModelOverride]:
        unknown = sorted(set(value) - set(TIER_NAMES))
        if unknown:
            raise ValueError(f"unknown tier(s) {unknown}; choose from {list(TIER_NAMES)}")
        return value


class ProfileOverrides(_Frozen):
    lead: ModelOverride = ModelOverride()
    worker: ModelOverride = ModelOverride()


class PriceOverride(_Frozen):
    """Your own price for a model, in USD per million tokens. It replaces the shipped price."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)
    cache_write: float | None = Field(default=None, ge=0)


class BudgetSettings(_Frozen):
    """Limits for one run; unset limits are not enforced. See docs/CONFIGURATION.md."""

    max_cost_usd: float | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_seconds: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_repair_rounds: int = Field(default=3, ge=0)


class DockerSettings(_Frozen):
    """The Docker sandbox (``execution.backend = "docker"``); see ``docs/SAFETY.md``.

    ``image`` and ``setup_image`` (unset: pick one per stack) must be images with the tools a
    project needs. ``network = "setup"`` gives only the install commands network access;
    ``"none"`` gives none at all (installs then fail, so dependencies must already be present).
    """

    image: str | None = None
    setup_image: str | None = None
    git_image: str = "alpine/git:2.54.0"
    network: Literal["setup", "none"] = "setup"
    memory: str = Field(default="2g", pattern=r"^[1-9][0-9]*[bkmgBKMG]?$")
    cpus: float = Field(default=2.0, gt=0, le=64)
    pids_limit: int = Field(default=512, ge=16)
    tmpfs_size: str = Field(default="512m", pattern=r"^[1-9][0-9]*[kmgKMG]$")


class ExecutionSettings(_Frozen):
    backend: Literal["local", "docker"] = "local"
    max_parallel_commands: int = Field(default=2, ge=1)
    docker: DockerSettings = DockerSettings()


class ParallelSettings(_Frozen):
    max_parallel_agents: int = Field(default=3, ge=1)
    max_rpm: int | None = Field(default=None, ge=1)


class DevToolsSettings(_Frozen):
    """The structured developer tools (Run Tests, Run Linter, ...). Timeouts are in seconds."""

    test_timeout: int = Field(default=300, ge=1, le=3600)
    lint_timeout: int = Field(default=120, ge=1, le=3600)
    typecheck_timeout: int = Field(default=180, ge=1, le=3600)
    format_timeout: int = Field(default=120, ge=1, le=3600)
    build_timeout: int = Field(default=300, ge=1, le=3600)
    coverage_timeout: int = Field(default=600, ge=1, le=3600)
    audit_timeout: int = Field(default=180, ge=1, le=3600)
    install_timeout: int = Field(default=900, ge=1, le=3600)
    max_failures: int = Field(default=20, ge=1, le=200)
    max_diagnostics: int = Field(default=50, ge=1, le=500)
    extra_executables: list[str] = []
    allow_network: bool = True


class ToolsSettings(_Frozen):
    dev: DevToolsSettings = DevToolsSettings()
    # Rarely used tools are held back behind Find Tool and Use Tool, so every call sends fewer
    # tool schemas; false gives every agent all of its groups' tools as separate tools (0.2.0).
    lazy: bool = True


class TrajectorySettings(_Frozen):
    """What the run's ``trajectory.jsonl`` keeps of every tool call (``docs/CONFIGURATION.md``)."""

    max_output_chars: int = Field(default=50_000, ge=10_000)
    max_mb: int = Field(default=50, ge=1)


class RuntimeSettings(_Frozen):
    """Background processes and the HTTP tool. Times are in seconds."""

    max_background_processes: int = Field(default=4, ge=1, le=32)
    process_lifetime_seconds: int = Field(default=1800, ge=1, le=86400)
    max_http_response_chars: int = Field(default=20000, ge=1000, le=200000)


class NetworkSettings(_Frozen):
    """Where agents' tools may reach beyond this run's own processes."""

    http_allowlist: list[str] = []


SEARCH_PROVIDERS = ("serper", "brave", "tavily")
# Environment variable each web search provider's API key is read from. Only the names appear
# in settings output; the values are kept in a private attribute and are never printed.
WEB_KEY_ENV = {"serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY"}


class WebSettings(_Frozen):
    """The opt-in web tools (Web Search, Fetch URL, Package Info). Off unless ``enabled``."""

    enabled: bool = False
    roles: list[str] = []  # only these teammates get the web tools; empty = every teammate
    search_provider: Literal["serper", "brave", "tavily"] | None = None
    allow_domains: list[str] = []  # when set, Fetch URL may only reach these hosts
    deny_domains: list[str] = []
    max_requests_per_run: int = Field(default=40, ge=1, le=1000)
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    max_download_bytes: int = Field(default=2_000_000, ge=10_000, le=20_000_000)
    max_page_chars: int = Field(default=20_000, ge=1000, le=200_000)

    @field_validator("roles", "allow_domains", "deny_domains")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [item.strip().lower() for item in value if item.strip()]


class KnowledgeSettings(_Frozen):
    """Where Search Docs looks besides the project's own markdown and the run's web cache, and
    the optional CrewAI knowledge sources (``sources``: embedded by ``embedder``'s provider)."""

    context_dirs: list[str] = []
    sources: list[str] = []  # files or directories of text documents; empty: CrewAI knowledge off
    embedder: EmbedderSettings | None = None  # required with ``sources``
    roles: list[str] = []  # only these teammates get the sources; empty = every teammate

    @field_validator("roles")
    @classmethod
    def _role_keys(cls, value: list[str]) -> list[str]:
        return [item.strip().lower().replace(" ", "_") for item in value if item.strip()]

    @model_validator(mode="after")
    def _sources_need_an_embedder(self) -> KnowledgeSettings:
        if self.sources and self.embedder is None:
            raise ValueError(
                "knowledge.sources needs knowledge.embedder (provider and model): the documents "
                "are sent to that provider to be embedded, which can cost money"
            )
        return self


class BrowserSettings(_Frozen):
    """The headless-browser tools (optional ``browser`` extra). Times are in seconds."""

    channel: Literal["chromium", "chrome", "msedge"] = "chromium"
    max_contexts: int = Field(default=2, ge=1, le=8)
    page_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_snapshot_chars: int = Field(default=20000, ge=1000, le=200000)
    max_console_entries: int = Field(default=100, ge=10, le=1000)


class GitSettings(_Frozen):
    """Git for the controller: a repository and a commit per stage in new projects."""

    enabled: bool = True  # --no-git turns it off
    author_name: str = "Engineering Team"
    author_email: str = "engineering-team@users.noreply.github.com"
    # A repository mode squashes the team's stage commits into one when the run succeeds.
    squash: bool = False


class VerifySettings(_Frozen):
    """Controller-run verification (a pipeline run's ``verify`` stage). Times are in seconds."""

    checks_file: str | None = None  # --checks: user-defined checks, kept outside the project
    static_required: bool = False  # a failing lint or type check fails the verification
    smoke: bool = True  # start the plan's run command and see that it does not crash
    smoke_seconds: int = Field(default=10, ge=1, le=300)
    smoke_required: bool = False
    timeout: int = Field(default=300, ge=1, le=3600)  # for commands the plan declares


class ReviewSettings(_Frozen):
    """The review stage: reviewers read the finished project and report findings."""

    # Findings at or above this severity are handed to the repair agent (one round, from the
    # shared ``budget.max_repair_rounds``) and the project is verified again.
    fail_on: Literal["low", "medium", "high", "critical"] = "high"


class AnalysisSettings(_Frozen):
    """Adopting an existing project: how the codebase analysts split and report on it."""

    # At most this many chunks (top-level packages or directories) are analysed side by side.
    max_chunks: int = Field(default=6, ge=1, le=20)
    # The size cap, in characters, of the codebase map that is put into agent context.
    context_chars: int = Field(default=8000, ge=1000, le=100_000)


class LeanSettings(_Frozen):
    """How lean a pipeline run is (``pipeline/lean.py``): its size and its optional stages."""

    # auto: decided from the request, the repository and the plan; otherwise fixed.
    size: Literal["auto", "small", "medium", "large"] = "auto"
    # auto: the devops and docs stages run only when the request asks for them.
    ceremony: Literal["auto", "always", "never"] = "auto"


class FixSettings(_Frozen):
    """Fix mode: how hard the team tries to reproduce a bug before it asks you."""

    # How many times the debugger may try to write a reproduction that fails because of the bug.
    max_repro_attempts: int = Field(default=3, ge=1, le=6)
    # Seconds the controller lets one run of the reproduction take (a hang is not a reproduction).
    repro_timeout: int = Field(default=120, ge=5, le=1800)


class MaintainSettings(_Frozen):
    """Maintenance tasks (``engineering-team maintain``) and the read-only ``review``."""

    # The most lines a ``refactor`` may change (added plus removed): a behaviour-preserving change
    # that grows past this is split into smaller ones, not reviewed in one piece.
    max_refactor_lines: int = Field(default=800, ge=10, le=100_000)
    # How many upgrades one ``upgrade-deps`` attempt may apply together before a failure is
    # bisected; 1 tries them strictly one by one.
    upgrade_group_size: int = Field(default=8, ge=1, le=50)


class UiSettings(_Frozen):
    """The local web UI (``engineering-team ui``): how many runs it starts and what it accepts."""

    # Runs the UI keeps going at once; another start is refused (429) until one ends. Each run is
    # a process of its own, and the workspace lock still lets only one write to a project.
    max_concurrent_runs: int = Field(default=2, ge=1, le=16)
    # The most bytes of uploaded request and reference files in one start request.
    max_upload_bytes: int = Field(default=2_000_000, ge=10_000, le=50_000_000)
    # The most bytes of any other request body (JSON).
    max_request_bytes: int = Field(default=200_000, ge=1_000, le=5_000_000)


TEAM_KEY = re.compile(r"[a-z][a-z0-9_]*")


class TeamOverride(_Frozen):
    """Changes to one teammate (``[team.<key>]`` or ``.engineering-team/team.yaml``).

    Every field is optional; unset fields keep the teammate's current value. A key that is not a
    built-in teammate adds a new one, which then needs ``role``, ``goal`` and ``backstory``.
    Values are checked when the roster is built (``team/registry.py``).
    """

    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    tier: Literal["lead", "worker", "cheap", "reviewer"] | None = None
    tool_groups: list[str] | None = None
    allow_delegation: bool | None = None
    max_iter: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    modes: list[str] | None = None
    stages: list[str] | None = None


class IntakeSettings(_Frozen):
    """Requirements intake: size caps for the request and ``--context-dir``, and clarification."""

    max_request_chars: int = Field(default=60_000, ge=500, le=1_000_000)
    max_context_files: int = Field(default=100, ge=1, le=2000)
    max_context_bytes: int = Field(default=2_000_000, ge=1000, le=100_000_000)
    max_questions: int = Field(default=5, ge=1, le=10)  # asked in one round per run
    question_timeout_seconds: int = Field(default=600, ge=5, le=3600)
