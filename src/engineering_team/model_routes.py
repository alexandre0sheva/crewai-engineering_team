"""Per-task routes and fallback models: the pure part (no CrewAI imports).

A route (``[models.routes."<intent>/<size>"]``) picks the tier a kind of task works at. The most
specific route wins: ``bugfix/large``, then ``bugfix/*``, then ``*/large``, then ``*/*``. A
fallback (``[models.fallbacks]``) names the models a tier moves to when its own model keeps
failing with a provider error, a rate limit or a timeout.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from engineering_team.model_routing import (
    PREFIX_TO_PROVIDER,
    PROVIDERS,
    VALUE_PRESET_NAME,
    ResolvedModel,
    facts_for,
    preset_tier,
    provider_prefix,
)

if TYPE_CHECKING:  # ``settings`` imports this module
    from engineering_team.settings.model_section import ModelRoute

ANY = "*"


@dataclass(frozen=True)
class RouteChoice:
    """The route a task matched, and the tier it gives one role."""

    key: str
    route: ModelRoute

    def tier_for(self, role: str, own_tier: str) -> str:
        """The tier ``role`` works at (``own_tier`` is the one it would have without a route)."""

        return self.route.roles.get(role) or self.route.tier or own_tier


def match_route(routes: Mapping[str, ModelRoute], intent: str, size: str) -> RouteChoice | None:
    """The most specific route for a task of ``intent`` and ``size``, if any."""

    for key in (f"{intent}/{size}", f"{intent}/{ANY}", f"{ANY}/{size}", f"{ANY}/{ANY}"):
        if key in routes:
            return RouteChoice(key, routes[key])
    return None


def describe_route(choice: RouteChoice) -> str:
    """``bugfix/large: debugger → reviewer`` (all roles: ``question/small: all → cheap``)."""

    parts = [f"all → {choice.route.tier}"] if choice.route.tier else []
    parts += [f"{role} → {tier}" for role, tier in sorted(choice.route.roles.items())]
    return f"{choice.key}: {', '.join(parts) or 'no change'}"


def fallback_models(
    entries: list[str], resolved: ResolvedModel, has_key: Callable[[str], bool]
) -> list[ResolvedModel]:
    """``resolved`` with each fallback entry's model, in order, without the entries that are the
    same model (or, for a provider name, have no preset). An entry is ``provider/model-id`` or a
    provider name, which stands for that provider's preset model of the same tier."""

    out: list[ResolvedModel] = []
    seen = {resolved.model}
    for entry in entries:
        if "/" in entry:
            model, effort, temperature = entry, None, None
        else:
            if entry == VALUE_PRESET_NAME or entry not in PROVIDERS:
                continue
            tier = preset_tier(entry, resolved.tier, has_key)
            if tier is None:  # Azure: your deployments, nothing to default to
                continue
            model, effort, temperature = tier.model, tier.reasoning_effort, tier.temperature
        if model in seen:
            continue
        seen.add(model)
        facts = facts_for(model)
        out.append(
            replace(
                resolved,
                model=model,
                reasoning_effort=effort,
                temperature=temperature,
                api=facts.api,
                context_window=facts.context_window,
                sources={"model": "models.fallbacks"},
            )
        )
    return out


def provider_of(model: str) -> str | None:
    return PREFIX_TO_PROVIDER.get(provider_prefix(model))
