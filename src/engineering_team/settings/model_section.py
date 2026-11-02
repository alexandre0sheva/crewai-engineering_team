"""The ``[models]`` table: tier and role overrides, per-task routes, and fallback models."""

from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engineering_team.contracts import Intent, TaskScale
from engineering_team.model_routing import PROVIDERS, TIER_NAMES, VALUE_PRESET_NAME
from engineering_team.settings.common import Effort

MODEL_ID = re.compile(r"[A-Za-z0-9_.-]+/\S+")
ROUTE_KEY = re.compile(r"(\*|[a-z_]+)/(\*|[a-z]+)")
MAX_FALLBACKS = 3


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
        if value is not None and not MODEL_ID.fullmatch(value):
            raise ValueError("must look like 'provider/model-id' (for example 'openai/gpt-6-luna')")
        return value


class ModelRoute(_Frozen):
    """The tiers a kind of task works at: ``tier`` for every teammate, ``roles`` for single
    ones (a teammate key, or a controller role such as ``triage``), which win over ``tier``."""

    tier: str | None = None
    roles: dict[str, str] = {}

    @field_validator("tier")
    @classmethod
    def _known_tier(cls, value: str | None) -> str | None:
        if value is not None and value not in TIER_NAMES:
            raise ValueError(f"unknown tier {value!r}; choose from {list(TIER_NAMES)}")
        return value

    @field_validator("roles")
    @classmethod
    def _known_role_tiers(cls, value: dict[str, str]) -> dict[str, str]:
        for role, tier in value.items():
            if tier not in TIER_NAMES:
                raise ValueError(
                    f"role {role!r} routes to unknown tier {tier!r}; choose from {list(TIER_NAMES)}"
                )
        return value


class ModelsSettings(_Frozen):
    tiers: dict[str, ModelOverride] = {}
    roles: dict[str, ModelOverride] = {}
    routes: dict[str, ModelRoute] = {}  # "<intent>/<size>" (either may be "*") -> tiers
    fallbacks: dict[str, list[str]] = {}  # tier -> models (or providers) to move to on errors
    fallback_cooldown_seconds: int = Field(default=60, ge=0)

    @field_validator("tiers")
    @classmethod
    def _known_tiers(cls, value: dict[str, ModelOverride]) -> dict[str, ModelOverride]:
        unknown = sorted(set(value) - set(TIER_NAMES))
        if unknown:
            raise ValueError(f"unknown tier(s) {unknown}; choose from {list(TIER_NAMES)}")
        return value

    @field_validator("routes")
    @classmethod
    def _route_keys(cls, value: dict[str, ModelRoute]) -> dict[str, ModelRoute]:
        intents, sizes = set(get_args(Intent)), set(get_args(TaskScale))
        for key in value:
            match = ROUTE_KEY.fullmatch(key)
            if not match or match[1] not in {"*", *intents} or match[2] not in {"*", *sizes}:
                raise ValueError(
                    f"route {key!r} must look like '<intent>/<size>' where either may be '*' "
                    f"(for example 'question/small' or 'bugfix/*'); intents: {sorted(intents)}, "
                    f"sizes: {list(get_args(TaskScale))}"
                )
        return value

    @field_validator("fallbacks", mode="before")
    @classmethod
    def _one_or_many(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {tier: [item] if isinstance(item, str) else item for tier, item in value.items()}
        return value

    @field_validator("fallbacks")
    @classmethod
    def _fallback_entries(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        providers = [name for name in PROVIDERS if name != VALUE_PRESET_NAME]
        for tier, entries in value.items():
            if tier not in TIER_NAMES:
                raise ValueError(f"unknown tier {tier!r}; choose from {list(TIER_NAMES)}")
            if len(entries) > MAX_FALLBACKS:
                raise ValueError(f"tier {tier!r} has more than {MAX_FALLBACKS} fallbacks")
            for entry in entries:
                if entry not in providers and not MODEL_ID.fullmatch(entry):
                    raise ValueError(
                        f"fallback {entry!r} of tier {tier!r} must be a 'provider/model-id' or a "
                        f"provider name ({', '.join(providers)}) for its preset model of the tier"
                    )
        return value
