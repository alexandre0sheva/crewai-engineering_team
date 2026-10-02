"""Provider presets, model facts, and resolution of a role to a concrete model.

This module is pure data and logic (no CrewAI imports) so it stays cheap to import and easy
to test. Model IDs here were checked against the providers' documentation on 2026-10-02:

* OpenAI: https://developers.openai.com/api/docs/models
* Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
* Google: https://ai.google.dev/gemini-api/docs/models and /pricing
* Ollama: https://ollama.com/library/qwen3.8

Re-verify them when updating; model lifecycles are short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

Provider = Literal["openai", "anthropic", "google", "ollama", "azure"]
PROVIDERS: Final[tuple[str, ...]] = ("openai", "anthropic", "google", "ollama", "azure")
REASONING_EFFORTS: Final[tuple[str, ...]] = ("none", "minimal", "low", "medium", "high", "xhigh")
TIER_NAMES: Final[tuple[str, ...]] = ("max", "lead", "reviewer", "worker", "cheap")
PROFILE_NAMES: Final[tuple[str, ...]] = ("standard", "smoke", "max-quality")

# CrewAI model-string prefix -> the provider whose preset/SDK/credentials apply.
PREFIX_TO_PROVIDER: Final[dict[str, str]] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "google": "google",
    "ollama": "ollama",
    "azure": "azure",
}

# Only OpenAI-style models accept ``reasoning_effort`` in CrewAI 1.15; Anthropic and Gemini
# use their own adaptive thinking and ignore it, so it is never sent to them.
REASONING_EFFORT_PREFIXES: Final[frozenset[str]] = frozenset({"openai", "azure"})

DEFAULT_OLLAMA_BASE_URL: Final = "http://localhost:11434"


@dataclass(frozen=True)
class ModelTier:
    """One rung of a provider preset: which model, and how hard it should think."""

    model: str
    reasoning_effort: str | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class ModelFacts:
    """Model-specific facts CrewAI cannot discover for newer model IDs."""

    # Real context window in tokens. CrewAI falls back to ~7K for model IDs it does not know,
    # which would trigger needless context summarisation.
    context_window: int | None = None
    # "responses" when the model cannot use tools over Chat Completions.
    api: str | None = None


# GPT-6 models call tools through the Responses API (gpt-6.1-sol has no tool calling on Chat
# Completions; gpt-6-luna only with reasoning_effort="none"). Prices live in data/pricing.toml.
MODEL_FACTS: Final[dict[str, ModelFacts]] = {
    "openai/gpt-6.1-sol": ModelFacts(1_050_000, "responses"),
    "openai/gpt-6-luna": ModelFacts(1_050_000, "responses"),
    # A conservative window: Ollama's server-side num_ctx is usually far below the model's 256K.
    "ollama/qwen3.8:27b": ModelFacts(context_window=32_768),
}

# Presets are organised by price. Tiers must never get cheaper as they get more capable, and
# only models worth their price are included:
#   luna ($0.1/$0.5) < gemini-3.8-flash ($0.75/$3.75) < gpt-6.1-sol = sonnet-5.5 ($2/$10)
#   < opus-5.5 ($4/$20). Local models cost nothing.
_OPENAI = {
    "max": ModelTier("openai/gpt-6.1-sol", "xhigh"),
    "lead": ModelTier("openai/gpt-6.1-sol", "high"),
    "reviewer": ModelTier("openai/gpt-6.1-sol", "medium"),
    "worker": ModelTier("openai/gpt-6-luna", "low"),
    "cheap": ModelTier("openai/gpt-6-luna", "none"),
}
# Anthropic has no model cheaper than Sonnet, so every tier except `max` uses it.
_ANTHROPIC = {
    "max": ModelTier("anthropic/claude-opus-5-5"),
    "lead": ModelTier("anthropic/claude-sonnet-5-5"),
    "reviewer": ModelTier("anthropic/claude-sonnet-5-5"),
    "worker": ModelTier("anthropic/claude-sonnet-5-5"),
    "cheap": ModelTier("anthropic/claude-sonnet-5-5"),
}
_GOOGLE = {tier: ModelTier("gemini/gemini-3.8-flash") for tier in TIER_NAMES}
# The best open model up to 30B parameters in the Ollama library at the time of writing.
_OLLAMA = {tier: ModelTier("ollama/qwen3.8:27b") for tier in TIER_NAMES}

# Azure is disabled by default (see Settings.enable_azure). Its model IDs are *deployment
# names* chosen by the user, so there is nothing to default.
PROVIDER_PRESETS: Final[dict[str, dict[str, ModelTier] | None]] = {
    "openai": _OPENAI,
    "anthropic": _ANTHROPIC,
    "google": _GOOGLE,
    "ollama": _OLLAMA,
    "azure": None,
}

# Credentials: environment variables (any one is enough) per provider. Values are never read
# for display, only their presence.
CREDENTIAL_ENV: Final[dict[str, tuple[str, ...]]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "ollama": (),
    "azure": (),
}

# Python module that must be importable for a provider's native CrewAI SDK, and the extra
# that installs it.
PROVIDER_SDK: Final[dict[str, tuple[str, str]]] = {
    "anthropic": ("anthropic", "anthropic"),
    "google": ("google.genai", "google"),
    "azure": ("azure.ai.inference", "azure"),
}


@dataclass(frozen=True)
class RoleSlot:
    """How a role class (lead or worker) is configured inside a profile."""

    tier: str
    max_iter: int


@dataclass(frozen=True)
class ProfileDefaults:
    lead: RoleSlot
    worker: RoleSlot
    docs_mcp: bool = True


PROFILE_DEFAULTS: Final[dict[str, ProfileDefaults]] = {
    "standard": ProfileDefaults(RoleSlot("lead", 35), RoleSlot("worker", 30)),
    "smoke": ProfileDefaults(RoleSlot("worker", 18), RoleSlot("cheap", 14), docs_mcp=False),
    "max-quality": ProfileDefaults(RoleSlot("max", 50), RoleSlot("lead", 40)),
}

# Roles that use the "lead" slot; every other role uses "worker".
LEAD_ROLES: Final[frozenset[str]] = frozenset({"engineering_lead"})


@dataclass(frozen=True)
class ResolvedModel:
    """Everything needed to construct an LLM for one role, plus where each value came from."""

    role: str
    slot: str
    tier: str
    model: str
    reasoning_effort: str | None
    temperature: float | None
    max_iter: int
    api: str | None
    context_window: int | None
    sources: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def provider_prefix(self) -> str:
        return provider_prefix(self.model)

    @property
    def provider(self) -> str | None:
        return PREFIX_TO_PROVIDER.get(self.provider_prefix)


def provider_prefix(model: str) -> str:
    return model.split("/", 1)[0].lower() if "/" in model else ""


def facts_for(model: str) -> ModelFacts:
    return MODEL_FACTS.get(model, ModelFacts())
