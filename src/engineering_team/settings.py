"""Typed, layered settings: one place that reads configuration.

Precedence, highest first: explicit overrides (CLI/API) > environment (``ENGINEERING_*``,
every 0.1.0 name still works) > project ``engineering-team.toml`` > user
``~/.config/engineering-team/config.toml`` > built-in defaults. Every value remembers which
layer set it, so ``describe()`` can explain the effective configuration.

This is the only module that reads the process environment for configuration (workspace
code reads it only to build the child-process environment).
"""

from __future__ import annotations

import difflib
import importlib.util
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
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
    PROVIDERS,
    REASONING_EFFORT_PREFIXES,
    REASONING_EFFORTS,
    TIER_NAMES,
    ModelTier,
    ResolvedModel,
    facts_for,
    provider_prefix,
)
from engineering_team.pricing import PriceTable, build_table, format_price

AZURE_DISABLED = (
    "Azure is disabled by default. Enable it with enable_azure = true in engineering-team.toml "
    "(or ENGINEERING_ENABLE_AZURE=true) and set your deployment names as models."
)
SMOKE_PROFILE_MARKER = "<!-- ENGINEERING_TEAM_PROFILE: smoke -->"
PROJECT_CONFIG_NAME = "engineering-team.toml"
DEFAULT = "default"

Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class SettingsError(ValueError):
    """A configuration problem, phrased for the person who has to fix it."""


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


class ExecutionSettings(_Frozen):
    backend: Literal["local", "docker"] = "local"
    max_parallel_commands: int = Field(default=2, ge=1)


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
    """Where Search Docs looks besides the project's own markdown and the run's web cache."""

    context_dirs: list[str] = []


class BrowserSettings(_Frozen):
    """The headless-browser tools (optional ``browser`` extra). Times are in seconds."""

    channel: Literal["chromium", "chrome", "msedge"] = "chromium"
    max_contexts: int = Field(default=2, ge=1, le=8)
    page_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_snapshot_chars: int = Field(default=20000, ge=1000, le=200000)
    max_console_entries: int = Field(default=100, ge=10, le=1000)


class Settings(_Frozen):
    provider: Literal["openai", "anthropic", "google", "ollama", "azure"] = "openai"
    profile: Literal["standard", "smoke", "max-quality"] = "standard"
    strategy: Literal["hierarchical", "pipeline", "single"] = "hierarchical"
    project_name: str = "mvp-app"
    workspace_root: str = "workspace"
    request: str | None = None
    request_file: str | None = None
    verbose: bool = True
    tracing: bool = False
    docs_mcp_urls: list[str] = []
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

    def resolve_model(self, role: str) -> ResolvedModel:
        """Resolve the concrete model for a role (preset → tier → profile → role overrides)."""

        slot_name = "lead" if role in LEAD_ROLES else "worker"
        slot = getattr(PROFILE_DEFAULTS[self.profile], slot_name)
        tier, tier_source = self._tier(slot.tier)

        values: dict[str, Any] = {
            "model": tier.model if tier else None,
            "reasoning_effort": tier.reasoning_effort if tier else None,
            "temperature": tier.temperature if tier else None,
            "max_iter": slot.max_iter,
            "context_window": None,
            "api": None,
        }
        sources = {name: tier_source for name in values}
        sources["max_iter"] = f"{self.profile} profile default"

        def apply(override: ModelOverride, key_prefix: str) -> None:
            for name in values:
                value = getattr(override, name)
                if value is not None:
                    values[name] = value
                    sources[name] = self.source_of(f"{key_prefix}.{name}")

        apply(self.models.tiers.get(slot.tier, ModelOverride()), f"models.tiers.{slot.tier}")
        profile_overrides = self.profiles.get(self.profile, ProfileOverrides())
        apply(getattr(profile_overrides, slot_name), f"profiles.{self.profile}.{slot_name}")
        apply(self.models.roles.get(role, ModelOverride()), f"models.roles.{role}")

        if values["model"] is None:
            raise SettingsError(
                f"Provider '{self.provider}' has no default models (for Azure they are your "
                f"deployment names). Set a model for the '{slot.tier}' tier "
                f'([models.tiers.{slot.tier}] model = "azure/<deployment>"), or '
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
        return ResolvedModel(role=role, slot=slot_name, tier=slot.tier, sources=sources, **values)

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

    def check_ready(self, *, require_credentials: bool) -> None:
        """Fail early, in one line each, when the chosen providers cannot work."""

        self.resolved_models()  # raises SettingsError for unresolvable models
        problems = self.sdk_problems()
        if require_credentials:
            problems += [f"{item}." for item in self.missing_credentials()]
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
            for key, value in _flatten(self.model_dump(exclude={"models", "profiles", "pricing"}))
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


# --- masking ---------------------------------------------------------------------------------

_SECRET_KEY = re.compile(r"(key|token|secret|password|credential)", re.IGNORECASE)


def mask_url(value: str) -> str:
    """Hide credentials embedded in a URL (userinfo and query string)."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return "****"
    if not parts.scheme or not parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[-1]
    netloc = f"***@{host}" if "@" in parts.netloc else host
    query = "***" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def mask_value(key: str, value: Any) -> str:
    if value is None or value == [] or value == "":
        return "(unset)"
    if _SECRET_KEY.search(key.rsplit(".", 1)[-1]):
        return "****"
    if isinstance(value, list):
        return ", ".join(mask_value(key, item) for item in value)
    if isinstance(value, str) and "://" in value:
        return mask_url(value)
    text = str(value)
    if len(text) > 80:  # e.g. a whole pasted request
        return f"{text[:60].splitlines()[0]}... ({len(text)} characters)"
    return text


# --- environment variables ------------------------------------------------------------------


def _bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError("expected true/false")


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ValueError("expected a whole number") from None


def _float(text: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise ValueError("expected a number") from None


def _list(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def _text(text: str) -> str:
    return text.strip()


def _build_env_table() -> dict[str, tuple[str, Callable[[str], Any]]]:
    table: dict[str, tuple[str, Callable[[str], Any]]] = {
        "ENGINEERING_PROVIDER": ("provider", _text),
        "ENGINEERING_RUN_PROFILE": ("profile", _text),
        "ENGINEERING_STRATEGY": ("strategy", _text),
        "ENGINEERING_PROJECT_NAME": ("project_name", _text),
        "ENGINEERING_WORKSPACE_ROOT": ("workspace_root", _text),
        "ENGINEERING_PROJECT_REQUEST": ("request", _text),
        "ENGINEERING_REQUEST_FILE": ("request_file", _text),
        "ENGINEERING_VERBOSE": ("verbose", _bool),
        "ENGINEERING_TRACING": ("tracing", _bool),
        "ENGINEERING_DOCS_MCP_URLS": ("docs_mcp_urls", _list),
        "ENGINEERING_COMMAND_ALLOWLIST": ("command_allowlist", _list),
        "ENGINEERING_SUBPROCESS_ENV_ALLOWLIST": ("subprocess_env_allowlist", _list),
        "ENGINEERING_OLLAMA_BASE_URL": ("ollama_base_url", _text),
        "ENGINEERING_ENABLE_AZURE": ("enable_azure", _bool),
        "ENGINEERING_ALLOW_WEB": ("web.enabled", _bool),
        "ENGINEERING_EXECUTION_BACKEND": ("execution.backend", _text),
        "ENGINEERING_MAX_PARALLEL_COMMANDS": ("execution.max_parallel_commands", _int),
        "ENGINEERING_MAX_PARALLEL": ("parallel.max_parallel_agents", _int),
        "ENGINEERING_MAX_RPM": ("parallel.max_rpm", _int),
        "ENGINEERING_BUDGET_MAX_COST_USD": ("budget.max_cost_usd", _float),
        "ENGINEERING_BUDGET_MAX_TOKENS": ("budget.max_tokens", _int),
        "ENGINEERING_BUDGET_MAX_WALL_SECONDS": ("budget.max_wall_seconds", _int),
        "ENGINEERING_BUDGET_MAX_TOOL_CALLS": ("budget.max_tool_calls", _int),
        "ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS": ("budget.max_repair_rounds", _int),
    }
    # 0.1.0 model variables: the standard names only affect the standard profile and the
    # ENGINEERING_SMOKE_* names only the smoke profile.
    for profile, prefix in (("standard", "ENGINEERING_"), ("smoke", "ENGINEERING_SMOKE_")):
        for slot, name in (("lead", "LEAD"), ("worker", "WORKER")):
            base = f"profiles.{profile}.{slot}"
            table[f"{prefix}{name}_MODEL"] = (f"{base}.model", _text)
            table[f"{prefix}{name}_REASONING_EFFORT"] = (f"{base}.reasoning_effort", _text)
            table[f"{prefix}{name}_MAX_ITER"] = (f"{base}.max_iter", _int)
    return table


ENV_SETTINGS = _build_env_table()
CONFIG_FILE_ENV = "ENGINEERING_CONFIG_FILE"


# --- layered loading ------------------------------------------------------------------------


def _nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    """``{"a.b": 1}`` -> ``{"a": {"b": 1}}``."""

    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        node = nested
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return nested


def _normalise(layer: dict[str, Any]) -> dict[str, Any]:
    """Expand ``roles.x = "provider/model"`` shorthand so leaf paths are uniform."""

    models = layer.get("models")
    if isinstance(models, dict) and isinstance(models.get("roles"), dict):
        models["roles"] = {
            role: {"model": spec} if isinstance(spec, str) else spec
            for role, spec in models["roles"].items()
        }
    return layer


def _merge(
    target: dict[str, Any],
    layer: Mapping[str, Any],
    label: str,
    sources: dict[str, str],
    prefix: str = "",
) -> None:
    for key, value in layer.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and isinstance(target.get(key), dict | type(None)):
            target.setdefault(key, {})
            _merge(target[key], value, label, sources, f"{path}.")
        else:
            target[key] = value
            sources[path] = label


def _build(
    layers: list[tuple[str, dict[str, Any]]],
    credential_names: frozenset[str],
    web_secrets: Mapping[str, str] | None = None,
) -> Settings:
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for label, layer in layers:
        _merge(merged, _normalise(_deep_copy(layer)), label, sources)
    try:
        settings = Settings.model_validate(merged)
    except ValidationError as exc:
        raise _friendly(exc, sources) from None
    settings._layers = layers
    settings._sources = sources
    settings._credential_names = credential_names
    settings._web_secrets = dict(web_secrets or {})
    return settings


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def _friendly(exc: ValidationError, sources: Mapping[str, str]) -> SettingsError:
    messages = []
    for error in exc.errors():
        path = ".".join(str(part) for part in error["loc"])
        if not path:  # a cross-field validator on the whole settings object
            messages.append(error["msg"].removeprefix("Value error, "))
            continue
        origin = _origin(path, sources)
        if error["type"] == "extra_forbidden":
            hint = ""
            if "." not in path:
                close = difflib.get_close_matches(path, Settings.model_fields, n=1)
                hint = f" Did you mean '{close[0]}'?" if close else ""
            messages.append(f"Unknown setting '{path}'{origin}.{hint}")
        else:
            message = error["msg"].removeprefix("Value error, ")
            given = error.get("input")
            shown = f" = {given!r}" if isinstance(given, str | int | float) else ""
            messages.append(f"Invalid setting '{path}'{shown}: {message}{origin}.")
    return SettingsError(" ".join(messages))


def _origin(path: str, sources: Mapping[str, str]) -> str:
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        source = sources.get(".".join(parts[:end]))
        if source:
            return f" (from {source})"
    # An unknown or invalid *table* is recorded by its leaves, e.g. "modles.roles.x".
    for key, source in sources.items():
        if key.startswith(f"{path}."):
            return f" (from {source})"
    return ""


def _read_toml(path: Path, label: str) -> tuple[str, dict[str, Any]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path} is not valid TOML: {exc}") from None
    except OSError as exc:
        raise SettingsError(f"Cannot read {path}: {exc.strerror or exc}") from None
    return f"{label} {path}", data


def user_config_path(env: Mapping[str, str], home: Path) -> Path:
    config_home = env.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else home / ".config"
    return base / "engineering-team" / "config.toml"


SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)


def secret_values(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Values of environment variables whose names look like credentials (``*KEY*``,
    ``*TOKEN*``, ``*SECRET*``, ``*PASSWORD*``), for scrubbing them out of logs and events."""

    environment = os.environ if env is None else env
    return frozenset(
        value for name, value in environment.items() if value and SECRET_ENV_NAME.search(name)
    )


def load_settings(
    *,
    overrides: Mapping[str, Any] | None = None,
    override_source: str = "cli",
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    config_file: str | Path | None = None,
) -> Settings:
    """Load settings from every layer. ``env`` defaults to the process environment."""

    environment = os.environ if env is None else env
    working_directory = cwd or Path.cwd()
    layers: list[tuple[str, dict[str, Any]]] = []

    user_path = user_config_path(environment, home or Path.home())
    if user_path.is_file():
        layers.append(_read_toml(user_path, "user config"))

    explicit = config_file or (environment.get(CONFIG_FILE_ENV) or "").strip() or None
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = working_directory / explicit_path
        if not explicit_path.is_file():
            raise SettingsError(f"Config file not found: {explicit_path}")
        layers.append(_read_toml(explicit_path, "config file"))
    else:
        project_path = working_directory / PROJECT_CONFIG_NAME
        if project_path.is_file():
            layers.append(_read_toml(project_path, "project config"))

    for name, (key, parse) in ENV_SETTINGS.items():
        raw = environment.get(name)
        if raw is None or not raw.strip():
            continue  # blank values count as unset
        try:
            value = parse(raw)
        except ValueError as exc:
            raise SettingsError(f"Invalid value for {name}: {exc} (got {raw.strip()!r}).") from None
        layers.append((f"env {name}", _nest({key: value})))

    if overrides:
        layers.append((override_source, _nest(overrides)))

    credential_names = frozenset(
        name
        for names in CREDENTIAL_ENV.values()
        for name in names
        if (environment.get(name) or "").strip()
    )
    web_secrets = {
        name: value
        for name in WEB_KEY_ENV.values()
        if (value := (environment.get(name) or "").strip())
    }
    return _build(layers, credential_names, web_secrets)


__all__ = [
    "REASONING_EFFORTS",
    "PROVIDERS",
    "SMOKE_PROFILE_MARKER",
    "Settings",
    "SettingsError",
    "SettingRow",
    "load_settings",
    "mask_url",
    "mask_value",
]


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            rows.extend(_flatten(item, f"{prefix}{key}."))
        return rows
    return [(prefix.rstrip("."), value)]
