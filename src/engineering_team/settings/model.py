"""The ``Settings`` model: validation across sections, model routing, and ``describe``."""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Mapping
from dataclasses import dataclass
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
from engineering_team.model_routing import (
    CREDENTIAL_ENV,
    DEFAULT_OLLAMA_BASE_URL,
    LEAD_ROLES,
    PREFIX_TO_PROVIDER,
    PROFILE_DEFAULTS,
    PROFILE_NAMES,
    PROVIDER_PRESETS,
    PROVIDER_SDK,
    REASONING_EFFORT_PREFIXES,
    ModelTier,
    ResolvedModel,
    facts_for,
    provider_prefix,
)
from engineering_team.pricing import PriceTable, build_table, format_price
from engineering_team.settings.common import (
    AZURE_DISABLED,
    DEFAULT,
    SMOKE_PROFILE_MARKER,
    SettingsError,
)
from engineering_team.settings.masking import _flatten, mask_value
from engineering_team.settings.sections import (
    TEAM_KEY,
    WEB_KEY_ENV,
    AnalysisSettings,
    BrowserSettings,
    BudgetSettings,
    ExecutionSettings,
    FixSettings,
    GitSettings,
    IntakeSettings,
    KnowledgeSettings,
    MaintainSettings,
    ModelOverride,
    ModelsSettings,
    NetworkSettings,
    ParallelSettings,
    PriceOverride,
    ProfileOverrides,
    ReviewSettings,
    RuntimeSettings,
    TeamOverride,
    ToolsSettings,
    UiSettings,
    VerifySettings,
    WebSettings,
    _Frozen,
)


class Settings(_Frozen):
    provider: Literal["openai", "anthropic", "google", "ollama", "azure"] = "openai"
    profile: Literal["standard", "smoke", "max-quality"] = "standard"
    strategy: Literal["pipeline", "single"] = "pipeline"
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
    enable_azure: bool = False
    models: ModelsSettings = ModelsSettings()
    profiles: dict[str, ProfileOverrides] = {}
    budget: BudgetSettings = BudgetSettings()
    pricing: dict[str, PriceOverride] = {}
    execution: ExecutionSettings = ExecutionSettings()
    parallel: ParallelSettings = ParallelSettings()
    tools: ToolsSettings = ToolsSettings()
    runtime: RuntimeSettings = RuntimeSettings()
    network: NetworkSettings = NetworkSettings()
    web: WebSettings = WebSettings()
    knowledge: KnowledgeSettings = KnowledgeSettings()
    browser: BrowserSettings = BrowserSettings()
    verify: VerifySettings = VerifySettings()
    git: GitSettings = GitSettings()
    intake: IntakeSettings = IntakeSettings()
    review: ReviewSettings = ReviewSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    fix: FixSettings = FixSettings()
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
        return _build(layers, self._credential_names, self._web_secrets)

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

    def _tier(self, name: str) -> tuple[ModelTier | None, str]:
        preset = PROVIDER_PRESETS[self.provider]
        return (preset[name] if preset else None), f"{self.provider} preset ({name})"

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
        model_tier, tier_source = self._tier(tier_name)

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

        rows = [
            SettingRow(key, mask_value(key, value), self.source_of(key))
            for key, value in _flatten(
                self.model_dump(exclude={"models", "profiles", "pricing", "team"})
            )
        ]
        rows.extend(
            SettingRow(
                f"pricing.{model}",
                format_price(price) + " (override)",
                self.source_of(f"pricing.{model}.input"),
            )
            for model, price in (
                (model, self.price_table().lookup(model)) for model in sorted(self.pricing)
            )
            if price is not None
        )
        table = self.price_table()
        for resolved in self.resolved_models():
            label = "lead" if resolved.slot == "lead" else resolved.role
            if resolved.role == "worker":
                label = "worker (default)"
            detail = (
                f"{resolved.model}, effort {_effort_text(resolved)}, max_iter {resolved.max_iter}"
            )
            if resolved.api:
                detail += f", api {resolved.api}"
            price = table.lookup(resolved.model)
            detail += f", {format_price(price)}" if price else ", price unknown"
            rows.append(SettingRow(f"model.{label}", detail, resolved.sources["model"]))
        for provider in sorted(self.used_providers()):
            names = CREDENTIAL_ENV.get(provider, ())
            if names:
                present = any(name in self._credential_names for name in names)
                rows.append(
                    SettingRow(
                        f"credentials.{'/'.join(names)}",
                        "set" if present else "MISSING",
                        "environment",
                    )
                )
        if self.web.enabled:
            keys_set = [name for name in WEB_KEY_ENV.values() if name in self._web_secrets]
            rows.extend(
                SettingRow(f"credentials.{name}", "set", "environment") for name in keys_set
            )
            if not keys_set:
                rows.append(
                    SettingRow(
                        f"credentials.{'/'.join(WEB_KEY_ENV.values())}", "MISSING", "environment"
                    )
                )
        return rows


@dataclass(frozen=True)
class SettingRow:
    key: str
    value: str
    source: str


def _effort_text(resolved: ResolvedModel) -> str:
    if resolved.reasoning_effort is None:
        return "provider default"
    if resolved.provider_prefix not in REASONING_EFFORT_PREFIXES:
        return f"{resolved.reasoning_effort} (ignored by this provider)"
    return resolved.reasoning_effort
