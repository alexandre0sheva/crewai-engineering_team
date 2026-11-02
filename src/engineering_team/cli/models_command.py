"""``models``: the provider presets, the model each tier resolves to, its price, and which keys
are set (by name; key values are never read for display)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from engineering_team.cli.context import fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.model_routing import CREDENTIAL_ENV, PROVIDERS, TIER_NAMES
from engineering_team.price_refresh import PriceDiff
from engineering_team.pricing import format_price
from engineering_team.settings import Settings, SettingsError


@dataclass(frozen=True)
class TierRow:
    preset: str
    tier: str
    model: str
    effort: str
    price: str
    key: str  # "set", "missing", or "-" (no key needed)
    active: bool


def preset_rows(settings: Settings) -> list[TierRow]:
    """Every preset's tiers as this configuration resolves them (its ``[models.tiers]`` and
    keys apply; profiles do not, so each tier shows its own model)."""

    table = settings.price_table()
    rows: list[TierRow] = []
    for preset in PROVIDERS:
        if preset == "azure" and not settings.enable_azure:
            continue
        try:
            chosen = settings.with_overrides(
                {"provider": preset, "profile": "standard"}, source="models"
            )
        except (SettingsError, ValueError):
            continue
        for tier in TIER_NAMES:
            try:
                resolved = chosen.resolve_model(f"tier:{tier}", tier=tier)
            except SettingsError:
                rows.append(TierRow(preset, tier, "(set your deployment)", "-", "-", "-", False))
                continue
            price = table.lookup(resolved.model)
            provider = resolved.provider or ""
            needs_key = bool(CREDENTIAL_ENV.get(provider))
            rows.append(
                TierRow(
                    preset=preset,
                    tier=tier,
                    model=resolved.model,
                    effort=resolved.reasoning_effort or "-",
                    price=format_price(price).removesuffix(" per MTok") if price else "unknown",
                    key=("set" if settings.has_key(provider) else "missing") if needs_key else "-",
                    active=preset == settings.provider,
                )
            )
    return rows


def key_rows(settings: Settings) -> dict[str, bool]:
    """Provider -> is one of its key variables set."""

    return {
        provider: settings.has_key(provider) for provider, names in CREDENTIAL_ENV.items() if names
    }


def models(
    ctx: typer.Context,
    refresh_prices: Annotated[
        bool,
        typer.Option(
            "--refresh-prices",
            help="Also fetch OpenRouter's models API (live, no key) and print where "
            "pricing.toml differs. Nothing is rewritten.",
        ),
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """List the model presets, the model of every tier, its price, and which keys are set."""

    g = get_globals(ctx)
    settings = load(g, config)
    rows = preset_rows(settings)
    keys = key_rows(settings)
    diffs = _refresh(settings) if refresh_prices else None
    if g.json:
        print_json(
            {
                "active": settings.provider,
                "presets": [asdict(row) for row in rows],
                "keys": keys,
                "price_changes": None if diffs is None else [asdict(d) for d in diffs],
            }
        )
        return
    console = g.console()
    table = Table("Preset", "Tier", "Model", "Effort", "$/MTok in/out", "Key", box=box.SIMPLE_HEAD)
    for row in rows:
        name = f"{row.preset} (active)" if row.active and row.tier == TIER_NAMES[0] else row.preset
        table.add_row(name, row.tier, row.model, row.effort, row.price, row.key)
    console.print(table)
    present = ", ".join(f"{p} {'set' if ok else 'missing'}" for p, ok in keys.items())
    console.print(f"Keys: {present}.", markup=False, soft_wrap=True)
    console.print(
        "Presets and the model screen behind them: docs/CONFIGURATION.md (Models).",
        markup=False,
        soft_wrap=True,
    )
    if diffs is not None:
        if not diffs:
            console.print("Prices: pricing.toml matches OpenRouter's API.", markup=False)
        for diff in diffs:
            console.print(f"Price change? {diff.text}", markup=False, soft_wrap=True)
        if diffs:
            console.print(
                "pricing.toml was not changed. Confirm on each row's source_url, then edit "
                "src/engineering_team/data/pricing.toml or add a [pricing] override.",
                markup=False,
                soft_wrap=True,
            )


def _refresh(settings: Settings) -> list[PriceDiff]:
    from engineering_team.price_refresh import compare, fetch_json

    try:
        return compare(
            settings.price_table().prices,
            fetch_json,
            quantizations=settings.openrouter.quantizations,
        )
    except (OSError, ValueError) as exc:
        fail(f"Could not fetch OpenRouter's models API: {exc}")
