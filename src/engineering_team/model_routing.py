"""Provider presets, model facts, and resolution of a role to a concrete model.

This module is pure data and logic (no CrewAI imports) so it stays cheap to import and easy
to test. Model IDs here were checked against the providers' documentation on 2026-11-01:

* OpenAI: https://developers.openai.com/api/docs/models
* Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
* Google: https://ai.google.dev/gemini-api/docs/models and /pricing
* OpenRouter: https://openrouter.ai/api/v1/models (and each model's /endpoints)
* Ollama: https://ollama.com/library/qwen3.8

Re-verify them when updating; model lifecycles are short.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal

Provider = Literal["openai", "anthropic", "google", "openrouter", "ollama", "azure", "value"]
# Every choice of ``provider``: the providers, plus ``value``, a preset mixing them.
PROVIDERS: Final[tuple[str, ...]] = (
    "openai",
    "anthropic",
    "google",
    "openrouter",
    "ollama",
    "azure",
    "value",
)
VALUE_PRESET_NAME: Final = "value"
REASONING_EFFORTS: Final[tuple[str, ...]] = ("none", "minimal", "low", "medium", "high", "xhigh")
TIER_NAMES: Final[tuple[str, ...]] = ("max", "lead", "reviewer", "worker", "cheap")
PROFILE_NAMES: Final[tuple[str, ...]] = ("standard", "smoke", "max-quality")

# CrewAI model-string prefix -> the provider whose preset/SDK/credentials apply.
PREFIX_TO_PROVIDER: Final[dict[str, str]] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "google": "google",
    "openrouter": "openrouter",
    "ollama": "ollama",
    "azure": "azure",
}

# Providers whose model names carry the model maker's own prefix (``openrouter/z-ai/glm-5.3``).
AGGREGATOR_PREFIXES: Final[frozenset[str]] = frozenset({"openrouter"})

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
    # False when the model is known not to call tools (measured, docs/BENCHMARKS.md): a stage
    # that must write then fails at its first answer with a hint instead of finishing empty.
    tool_calling: bool | None = None


# GPT-6 models call tools through the Responses API (gpt-6.1-sol has no tool calling on Chat
# Completions; gpt-6-luna only with reasoning_effort="none"). Prices live in data/pricing.toml.
MODEL_FACTS: Final[dict[str, ModelFacts]] = {
    "openai/gpt-6.1-sol": ModelFacts(1_050_000, "responses"),
    "openai/gpt-6.1-terra": ModelFacts(1_050_000, "responses"),
    "openai/gpt-6-luna": ModelFacts(1_050_000, "responses"),
    "anthropic/claude-opus-5-5": ModelFacts(1_000_000),
    "anthropic/claude-sonnet-5-5": ModelFacts(1_000_000),
    "anthropic/claude-haiku-5-5": ModelFacts(1_000_000),
    "gemini/gemini-4-argon": ModelFacts(1_048_576),
    "gemini/gemini-3.8-flash": ModelFacts(1_048_576),
    # OpenRouter: the context OpenRouter's models API reports (Chat Completions, tools).
    "openrouter/deepseek/deepseek-v4.1-flash": ModelFacts(1_048_576),
    "openrouter/z-ai/glm-5.3-flash": ModelFacts(1_048_576),
    "openrouter/z-ai/glm-5.3": ModelFacts(1_048_576),
    "openrouter/minimax/minimax-m3.1-flash": ModelFacts(1_048_576),
    "openrouter/qwen/qwen3-coder-next": ModelFacts(262_144),
    "openrouter/moonshotai/kimi-k2.7-code": ModelFacts(262_144),
    "openrouter/mistralai/devstral-2512": ModelFacts(262_144),
    # A conservative window: Ollama's server-side num_ctx is usually far below the model's 256K.
    "ollama/qwen3.8:27b": ModelFacts(context_window=32_768),
    # Answered the probe task in plain text with no tool call (docs/BENCHMARKS.md, local models).
    "ollama/gemma3:270m": ModelFacts(tool_calling=False),
    "hosted_vllm/deepseek-r1-distill-llama-8b-mlx": ModelFacts(tool_calling=False),
}

# Presets are chosen by the model screen of 2026-11-01 (docs/BENCHMARKS.md, Model screen): per
# provider the cheapest model that passed for `cheap`/`worker`, the best value that passed for
# `reviewer`/`lead`, the strongest for `max`. Tiers never get cheaper as they get more capable.
# Output price per million tokens: luna $0.5 = haiku $0.5 < minimax-m3.1-flash $0.6
#   < deepseek-v4.1-flash $1 < gemini-3.8-flash $3.75 < kimi-k2.7-code $4 < terra $5
#   < sol = sonnet-5.5 $10 < opus-5.5 $20. Local models cost nothing. Opus (`max` only) was not
# screened: its price rules it out of every other tier (plan T25). Provisional until plan T33.
_OPENAI = {
    "max": ModelTier("openai/gpt-6.1-sol", "xhigh"),
    "lead": ModelTier("openai/gpt-6.1-sol", "high"),
    "reviewer": ModelTier("openai/gpt-6.1-terra", "medium"),
    "worker": ModelTier("openai/gpt-6-luna", "low"),
    "cheap": ModelTier("openai/gpt-6-luna", "none"),
}
_ANTHROPIC = {
    "max": ModelTier("anthropic/claude-opus-5-5"),
    "lead": ModelTier("anthropic/claude-sonnet-5-5"),
    "reviewer": ModelTier("anthropic/claude-sonnet-5-5"),
    "worker": ModelTier("anthropic/claude-haiku-5-5"),
    "cheap": ModelTier("anthropic/claude-haiku-5-5"),
}
# Gemini 3.8 Flash passed, at the highest cost per success of the screen; Gemini 4 Argon was not
# screened, so it is in no preset.
_GOOGLE = {tier: ModelTier("gemini/gemini-3.8-flash") for tier in TIER_NAMES}
_OPENROUTER = {
    "max": ModelTier("openrouter/moonshotai/kimi-k2.7-code"),
    "lead": ModelTier("openrouter/moonshotai/kimi-k2.7-code"),
    "reviewer": ModelTier("openrouter/deepseek/deepseek-v4.1-flash"),
    "worker": ModelTier("openrouter/minimax/minimax-m3.1-flash"),
    "cheap": ModelTier("openrouter/minimax/minimax-m3.1-flash"),
}
# The best open model up to 30B parameters in the Ollama library at the time of writing.
_OLLAMA = {tier: ModelTier("ollama/qwen3.8:27b") for tier in TIER_NAMES}

# Azure is disabled by default (see Settings.enable_azure). Its model IDs are *deployment
# names* chosen by the user, so there is nothing to default.
PROVIDER_PRESETS: Final[dict[str, dict[str, ModelTier] | None]] = {
    "openai": _OPENAI,
    "anthropic": _ANTHROPIC,
    "google": _GOOGLE,
    "openrouter": _OPENROUTER,
    "ollama": _OLLAMA,
    "azure": None,
}

# The mixed `value` preset: per tier, the providers' preset models in order of measured cost per
# success in the screen (unscreened Opus after the screened `max` models; Gemini, the dearest,
# last). A tier takes the first whose provider has a key, so it falls back tier by tier to whatever
# key is configured (with no key at all, the first, and the missing key is reported).
VALUE_PRESET: Final[dict[str, tuple[ModelTier, ...]]] = {
    "max": (_OPENAI["max"], _OPENROUTER["max"], _ANTHROPIC["max"], _GOOGLE["max"]),
    "lead": (_OPENAI["lead"], _OPENROUTER["lead"], _ANTHROPIC["lead"], _GOOGLE["lead"]),
    "reviewer": (
        _OPENROUTER["reviewer"],
        _OPENAI["reviewer"],
        _ANTHROPIC["reviewer"],
        _GOOGLE["reviewer"],
    ),
    "worker": (_OPENAI["worker"], _ANTHROPIC["worker"], _OPENROUTER["worker"], _GOOGLE["worker"]),
    "cheap": (_OPENAI["cheap"], _ANTHROPIC["cheap"], _OPENROUTER["cheap"], _GOOGLE["cheap"]),
}


def preset_tier(preset: str, tier: str, has_key: Callable[[str], bool]) -> ModelTier | None:
    """The model of ``tier`` in ``preset``; ``has_key(provider)`` says whether a key is set
    (used by `value` only). ``None`` when the preset has no default models (Azure)."""

    if preset == VALUE_PRESET_NAME:
        chain = VALUE_PRESET[tier]
        for candidate in chain:
            provider = PREFIX_TO_PROVIDER.get(provider_prefix(candidate.model))
            if provider and has_key(provider):
                return candidate
        return chain[0]
    tiers = PROVIDER_PRESETS[preset]
    return tiers[tier] if tiers else None


# Credentials: environment variables (any one is enough) per provider. Values are never read
# for display, only their presence.
CREDENTIAL_ENV: Final[dict[str, tuple[str, ...]]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
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
