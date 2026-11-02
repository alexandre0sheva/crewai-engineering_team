"""``Settings.describe``: every effective value with where it came from, secrets masked."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engineering_team.model_routes import RouteChoice, describe_route
from engineering_team.model_routing import CREDENTIAL_ENV, REASONING_EFFORT_PREFIXES, ResolvedModel
from engineering_team.pricing import format_price
from engineering_team.settings.masking import _flatten, mask_value
from engineering_team.settings.sections import WEB_KEY_ENV

if TYPE_CHECKING:
    from engineering_team.settings.model import Settings


@dataclass(frozen=True)
class SettingRow:
    key: str
    value: str
    source: str


def describe_settings(settings: Settings) -> list[SettingRow]:
    """The rows of ``config show``: values, price overrides, resolved models, credentials."""

    rows = [
        SettingRow(key, mask_value(key, value), settings.source_of(key))
        for key, value in _flatten(
            settings.model_dump(exclude={"models", "profiles", "pricing", "team"})
        )
    ]
    table = settings.price_table()
    if settings.models.fallbacks:
        rows.append(
            SettingRow(
                "models.fallback_cooldown_seconds",
                str(settings.models.fallback_cooldown_seconds),
                settings.source_of("models.fallback_cooldown_seconds"),
            )
        )
    rows.extend(
        SettingRow(f"models.routes.{key}", describe_route(RouteChoice(key, route)),
                   settings.source_of(f"models.routes.{key}.{'tier' if route.tier else 'roles'}"))
        for key, route in settings.models.routes.items()
    )  # fmt: skip
    rows.extend(
        SettingRow(f"models.fallbacks.{tier}", ", ".join(models),
                   settings.source_of(f"models.fallbacks.{tier}"))
        for tier, models in settings.models.fallbacks.items()
    )  # fmt: skip
    rows.extend(
        SettingRow(
            f"pricing.{model}",
            format_price(price) + " (override)",
            settings.source_of(f"pricing.{model}.input"),
        )
        for model, price in ((model, table.lookup(model)) for model in sorted(settings.pricing))
        if price is not None
    )
    for resolved in settings.resolved_models():
        label = "lead" if resolved.slot == "lead" else resolved.role
        if resolved.role == "worker":
            label = "worker (default)"
        detail = f"{resolved.model}, effort {_effort_text(resolved)}, max_iter {resolved.max_iter}"
        if resolved.api:
            detail += f", api {resolved.api}"
        price = table.lookup(resolved.model)
        detail += f", {format_price(price)}" if price else ", price unknown"
        rows.append(SettingRow(f"model.{label}", detail, resolved.sources["model"]))
    for provider in sorted(settings.used_providers()):
        names = CREDENTIAL_ENV.get(provider, ())
        if names:
            rows.append(
                SettingRow(
                    f"credentials.{'/'.join(names)}",
                    "set" if settings.has_key(provider) else "MISSING",
                    "environment",
                )
            )
    if settings.web.enabled:
        keys_set = [name for web, name in WEB_KEY_ENV.items() if settings.web_api_key(web)]
        rows.extend(SettingRow(f"credentials.{name}", "set", "environment") for name in keys_set)
        if not keys_set:
            rows.append(
                SettingRow(
                    f"credentials.{'/'.join(WEB_KEY_ENV.values())}", "MISSING", "environment"
                )
            )
    return rows


def _effort_text(resolved: ResolvedModel) -> str:
    if resolved.reasoning_effort is None:
        return "provider default"
    if resolved.provider_prefix not in REASONING_EFFORT_PREFIXES:
        return f"{resolved.reasoning_effort} (ignored by this provider)"
    return resolved.reasoning_effort
