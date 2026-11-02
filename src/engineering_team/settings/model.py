"""The ``Settings`` model: validation across sections, model routing, and ``describe``."""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    PrivateAttr,
    field_validator,
    model_validator,
)

from engineering_team.extensions.config import (
    NAME as EXTENSION_NAME,
)
from engineering_team.extensions.config import (
    ConventionsSettings,
    HooksSettings,
    McpServer,
    PluginSettings,
)
from engineering_team.model_routes import RouteChoice, fallback_models, match_route
from engineering_team.model_routing import (
    CREDENTIAL_ENV,
    DEFAULT_OLLAMA_BASE_URL,
    LEAD_ROLES,
    PREFIX_TO_PROVIDER,
    PROFILE_DEFAULTS,
    PROFILE_NAMES,
    PROVIDER_SDK,
    VALUE_PRESET_NAME,
    ModelTier,
    Provider,
    ResolvedModel,
    facts_for,
    preset_tier,
    provider_prefix,
)
from engineering_team.pricing import PriceTable, build_table
from engineering_team.settings.common import (
    AZURE_DISABLED,
    DEFAULT,
    SMOKE_PROFILE_MARKER,
    SettingsError,
)
from engineering_team.settings.context_pack import ContextSettings
from engineering_team.settings.describe import SettingRow
from engineering_team.settings.lsp import LspSettings
from engineering_team.settings.model_section import ModelOverride, ModelsSettings, _Frozen
from engineering_team.settings.openrouter import OpenRouterSettings
from engineering_team.settings.sections import (
    TEAM_KEY,
    WEB_KEY_ENV,
    AcceptanceSettings,
    AnalysisSettings,
    AttemptsSettings,
    BrowserSettings,
    BudgetSettings,
    DelegationSettings,
    ExecutionSettings,
    FixSettings,
    GitSettings,
    IntakeSettings,
    KnowledgeSettings,
    LadderSettings,
    LeanSettings,
    LearningSettings,
    MaintainSettings,
    MemorySettings,
    NetworkSettings,
    ParallelSettings,
    PriceOverride,
    ProfileOverrides,
    ReviewSettings,
    RuntimeSettings,
    TeamOverride,
    ToolsSettings,
    TrajectorySettings,
    UiSettings,
    VerifySettings,
    WebSettings,
)


class Settings(_Frozen):
    provider: Provider = "openai"  # a provider's preset, or `value` (mixed)
    profile: Literal["standard", "smoke", "max-quality"] = "standard"
    strategy: Literal["auto", "pipeline", "single", "team"] = "auto"
    project_name: str = "mvp-app"
    workspace_root: str = "workspace"
    request: str | None = None
    request_file: str | None = None
    verbose: bool = True
    tracing: bool = False
    docs_mcp_urls: list[str] = []  # alias: attached to every teammate with ``mcp:docs``
    mcp: dict[str, McpServer] = {}
    command_allowlist: list[str] = []
    subprocess_env_allowlist: list[str] = []
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    openrouter: OpenRouterSettings = OpenRouterSettings()
    enable_azure: bool = False
    models: ModelsSettings = ModelsSettings()
    profiles: dict[str, ProfileOverrides] = {}
    budget: BudgetSettings = BudgetSettings()
    pricing: dict[str, PriceOverride] = {}
    execution: ExecutionSettings = ExecutionSettings()
    parallel: ParallelSettings = ParallelSettings()
    tools: ToolsSettings = ToolsSettings()
    runtime: RuntimeSettings = RuntimeSettings()
    lsp: LspSettings = LspSettings()
    trajectory: TrajectorySettings = TrajectorySettings()
    memory: MemorySettings = MemorySettings()
    learning: LearningSettings = LearningSettings()
    context: ContextSettings = ContextSettings()
    network: NetworkSettings = NetworkSettings()
    web: WebSettings = WebSettings()
    knowledge: KnowledgeSettings = KnowledgeSettings()
    browser: BrowserSettings = BrowserSettings()
    verify: VerifySettings = VerifySettings()
    git: GitSettings = GitSettings()
    intake: IntakeSettings = IntakeSettings()
    review: ReviewSettings = ReviewSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    lean: LeanSettings = LeanSettings()
    delegation: DelegationSettings = DelegationSettings()
    ladder: LadderSettings = LadderSettings()
    attempts: AttemptsSettings = AttemptsSettings()
    fix: FixSettings = FixSettings()
    acceptance: AcceptanceSettings = AcceptanceSettings()
    maintain: MaintainSettings = MaintainSettings()
    ui: UiSettings = UiSettings()
    team_profile: Literal["full", "minimal"] = "full"  # minimal: no review, DevOps, or docs stage
    team: dict[str, TeamOverride] = {}
    team_file: str | None = None  # default: ./.engineering-team/team.yaml when it exists
    conventions: ConventionsSettings = ConventionsSettings()
    conventions_file: str | None = None  # extra conventions for any project, e.g. a style guide
    plugins: PluginSettings = PluginSettings()
    allow_project_plugins: bool = False  # run .engineering-team/tools/*.py: code execution
    hooks: HooksSettings = HooksSettings()

    _layers: list[tuple[str, dict[str, Any]]] = PrivateAttr(default_factory=list)
    _sources: dict[str, str] = PrivateAttr(default_factory=dict)
    _credential_names: frozenset[str] = PrivateAttr(default_factory=frozenset)
    _web_secrets: dict[str, str] = PrivateAttr(default_factory=dict)
    _task: tuple[str, str] | None = PrivateAttr(default=None)  # (intent, size) of the run

    @field_validator("pricing")
    @classmethod
    def _qualified_price_keys(cls, value: dict[str, PriceOverride]) -> dict[str, PriceOverride]:
        for key in value:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/(\*|\S+)", key):
                raise ValueError(
                    f"price override {key!r} must look like 'provider/model-id' "
                    "(or 'provider/*' for every model of a provider)"
                )
        return value

    @field_validator("team")
    @classmethod
    def _team_keys(cls, value: dict[str, TeamOverride]) -> dict[str, TeamOverride]:
        for key in value:
            if not TEAM_KEY.fullmatch(key):
                raise ValueError(
                    f"teammate key {key!r} must be lowercase letters, digits and _ "
                    "(for example 'data_engineer')"
                )
        return value

    @field_validator("mcp")
    @classmethod
    def _mcp_names(cls, value: dict[str, McpServer]) -> dict[str, McpServer]:
        for key in value:
            if not EXTENSION_NAME.fullmatch(key):
                raise ValueError(
                    f"MCP server name {key!r} must be lowercase letters, digits and _ "
                    "(for example 'docs'); teammates refer to it as 'mcp:<name>'"
                )
        return value

    @field_validator("profiles")
    @classmethod
    def _known_profiles(cls, value: dict[str, ProfileOverrides]) -> dict[str, ProfileOverrides]:
        unknown = sorted(set(value) - set(PROFILE_NAMES))
        if unknown:
            raise ValueError(f"unknown profile(s) {unknown}; choose from {list(PROFILE_NAMES)}")
        return value

    # -- sources ----------------------------------------------------------------------------

    def source_of(self, key: str) -> str:
        """Which layer set ``key`` (dotted), or ``default``."""

        parts = key.split(".")
        for end in range(len(parts), 0, -1):
            source = self._sources.get(".".join(parts[:end]))
            if source:
                return source
        return DEFAULT

    def with_overrides(self, overrides: Mapping[str, Any], *, source: str) -> Settings:
        """A new Settings with one more, highest-precedence layer."""

        from engineering_team.settings.loading import _build, _nest  # loading builds Settings

        layers = [*self._layers, (source, _nest(overrides))]
        built = _build(layers, self._credential_names, self._web_secrets)
        built._task = self._task
        return built

    def for_task(self, intent: str, size: str) -> Settings:
        """These settings for a run of this kind of task: ``models.routes`` then apply."""

        routed = self.model_copy()
        routed._task = (intent, size)
        return routed

    def route(self) -> RouteChoice | None:
        """The ``models.routes`` entry that applies to this run's task, if any."""

        if self._task is None:
            return None
        return match_route(self.models.routes, *self._task)

    def fallbacks_for(self, resolved: ResolvedModel) -> list[ResolvedModel]:
        """The models ``resolved`` moves to when it keeps failing (``models.fallbacks``)."""

        entries = self.models.fallbacks.get(resolved.tier, [])
        return fallback_models(entries, resolved, self.has_key)

    def web_api_key(self, provider: str) -> str | None:
        """The API key of a web search provider (from its environment variable), if set."""

        return self._web_secrets.get(WEB_KEY_ENV[provider])

    def for_request(self, requirements: str) -> Settings:
        """Apply the request's smoke marker, but only when no layer chose a profile."""

        if SMOKE_PROFILE_MARKER in requirements and self.source_of("profile") == DEFAULT:
            return self.with_overrides({"profile": "smoke"}, source="request marker")
        return self

    # -- model routing ----------------------------------------------------------------------

    @model_validator(mode="after")
    def _azure_must_be_enabled(self) -> Settings:
        if self.provider == "azure" and not self.enable_azure:
            raise ValueError(AZURE_DISABLED)
        return self

    @property
    def docs_mcp_enabled(self) -> bool:
        return PROFILE_DEFAULTS[self.profile].docs_mcp

    def has_key(self, provider: str) -> bool:
        """Is a credential of ``provider`` set (by name; values are never read)?"""

        return any(name in self._credential_names for name in CREDENTIAL_ENV.get(provider, ()))

    def _tier(self, name: str) -> tuple[ModelTier | None, str]:
        tier = preset_tier(self.provider, name, self.has_key)
        source = f"{self.provider} preset ({name})"
        if self.provider == VALUE_PRESET_NAME and tier is not None:
            source = f"value preset ({name}, {PREFIX_TO_PROVIDER[provider_prefix(tier.model)]})"
        return tier, source

    def resolve_model(
        self, role: str, *, tier: str | None = None, max_iter: int | None = None
    ) -> ResolvedModel:
        """Resolve the concrete model for a role (preset → tier → profile → role overrides).

        ``tier`` and ``max_iter`` are the teammate's own (``team/registry.py``). A ``lead``
        tier uses the profile's lead slot. Only the ``standard`` profile maps the other tiers to
        their own model tiers; ``smoke`` and ``max-quality`` fix the models of both slots, so a
        cheap run stays cheap. ``models.roles`` overrides still win over both.
        """

        slot_name = "lead" if tier == "lead" or (tier is None and role in LEAD_ROLES) else "worker"
        slot = getattr(PROFILE_DEFAULTS[self.profile], slot_name)
        tier_name = tier if tier is not None and self.profile == "standard" else slot.tier
        routed = self.route() if self.profile == "standard" else None
        if routed is not None:
            tier_name = routed.tier_for(role, tier_name)
        model_tier, tier_source = self._tier(tier_name)
        if routed is not None:
            tier_source += f" via route {routed.key}"

        values: dict[str, Any] = {
            "model": model_tier.model if model_tier else None,
            "reasoning_effort": model_tier.reasoning_effort if model_tier else None,
            "temperature": model_tier.temperature if model_tier else None,
            "max_iter": max_iter or slot.max_iter,
            "context_window": None,
            "api": None,
        }
        sources = {name: tier_source for name in values}
        sources["max_iter"] = "team setting" if max_iter else f"{self.profile} profile default"

        def apply(override: ModelOverride, key_prefix: str) -> None:
            for name in values:
                value = getattr(override, name)
                if value is not None:
                    values[name] = value
                    sources[name] = self.source_of(f"{key_prefix}.{name}")

        apply(self.models.tiers.get(tier_name, ModelOverride()), f"models.tiers.{tier_name}")
        profile_overrides = self.profiles.get(self.profile, ProfileOverrides())
        apply(getattr(profile_overrides, slot_name), f"profiles.{self.profile}.{slot_name}")
        apply(self.models.roles.get(role, ModelOverride()), f"models.roles.{role}")

        if values["model"] is None:
            raise SettingsError(
                f"Provider '{self.provider}' has no default models (for Azure they are your "
                f"deployment names). Set a model for the '{tier_name}' tier "
                f'([models.tiers.{tier_name}] model = "azure/<deployment>"), or '
                "ENGINEERING_LEAD_MODEL / ENGINEERING_WORKER_MODEL."
            )

        if provider_prefix(values["model"]) == "azure" and not self.enable_azure:
            raise SettingsError(AZURE_DISABLED)
        facts = facts_for(values["model"])
        if values["api"] is None and facts.api:
            values["api"], sources["api"] = facts.api, "model facts"
        if values["context_window"] is None and facts.context_window:
            values["context_window"], sources["context_window"] = (
                facts.context_window,
                "model facts",
            )
        return ResolvedModel(role=role, slot=slot_name, tier=tier_name, sources=sources, **values)

    def resolved_models(self) -> list[ResolvedModel]:
        """The lead, a representative worker, and every explicitly configured role."""

        roles = ["engineering_lead", "worker", *sorted(self.models.roles)]
        return [self.resolve_model(role) for role in dict.fromkeys(roles)]

    # -- environment checks -----------------------------------------------------------------

    def used_providers(self) -> set[str]:
        providers = set()
        for resolved in self.resolved_models():
            provider = PREFIX_TO_PROVIDER.get(provider_prefix(resolved.model))
            if provider:
                providers.add(provider)
        return providers

    def sdk_problems(self) -> list[str]:
        """Missing optional provider SDKs, as actionable one-liners."""

        problems = []
        for provider in sorted(self.used_providers() & set(PROVIDER_SDK)):
            module, extra = PROVIDER_SDK[provider]
            try:
                available = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                available = False
            if not available:
                problems.append(
                    f"The '{provider}' models need their SDK: install the extra with "
                    f'`uv sync --extra {extra}` (or `pip install "engineering_team[{extra}]"`).'
                )
        return problems

    def missing_credentials(self) -> list[str]:
        """Provider credentials that are not set (checked by name; values are never read)."""

        missing = []
        for provider in sorted(self.used_providers()):
            names = CREDENTIAL_ENV.get(provider, ())
            if names and not any(name in self._credential_names for name in names):
                missing.append(f"{' or '.join(names)} is not set (needed by {provider} models)")
        return missing

    def missing_embedder_credentials(self) -> list[str]:
        """The knowledge embedder's credential, when it needs one that is not set."""

        embedder = self.knowledge.embedder
        provider = {"openai": "openai", "google-generativeai": "google"}.get(
            embedder.provider if embedder else ""
        )
        names = CREDENTIAL_ENV.get(provider or "", ())
        if names and not any(name in self._credential_names for name in names):
            return [
                f"{' or '.join(names)} is not set (needed to embed knowledge.sources with "
                f"{embedder.provider if embedder else provider})"
            ]
        return []

    def check_ready(self, *, require_credentials: bool) -> None:
        """Fail early, in one line each, when the chosen providers cannot work."""

        from engineering_team.extensions.checks import check_extensions
        from engineering_team.team import build_roster

        self.resolved_models()  # raises SettingsError for unresolvable models
        build_roster(self)  # raises TeamError (a ValueError) for an invalid team definition
        check_extensions(self)  # raises ValueError for a missing conventions file or source
        problems = self.sdk_problems()
        if require_credentials:
            problems += [f"{item}." for item in self.missing_credentials()]
            problems += [f"{item}." for item in self.missing_embedder_credentials()]
        if problems:
            raise SettingsError(" ".join(problems))

    def price_table(self) -> PriceTable:
        """The shipped prices with this configuration's ``[pricing]`` overrides applied."""

        return build_table(
            {model: override.model_dump() for model, override in self.pricing.items()}
        )

    # -- explanation ------------------------------------------------------------------------

    def describe(self) -> list[SettingRow]:
        """Every effective value with its source; secrets and URL credentials are masked."""

        from engineering_team.settings.describe import describe_settings

        return describe_settings(self)
