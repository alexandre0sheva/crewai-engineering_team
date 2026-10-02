from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from engineering_team.pricing import (
    Price,
    PriceTable,
    build_table,
    default_prices,
    estimate_cost,
    format_price,
    normalize_model,
)
from engineering_team.settings import SettingsError, load_settings

DAY = date(2026, 10, 2)


def test_every_shipped_price_cites_its_source_and_the_date_it_was_checked() -> None:
    rows = default_prices()

    assert rows
    for price in rows:
        assert price.as_of is not None and price.as_of <= date.today(), price.model
        assert price.input >= 0 and price.output >= 0, price.model
        if not price.is_free:  # local models have no provider page to cite
            assert price.source_url.startswith("https://"), price.model


def test_the_shipped_table_prices_every_preset_model() -> None:
    table = build_table()

    for model in (
        "openai/gpt-6.1-sol",
        "openai/gpt-6-luna",
        "anthropic/claude-opus-5-5",
        "anthropic/claude-sonnet-5-5",
        "gemini/gemini-3.8-flash",
        "ollama/qwen3.8:27b",
    ):
        assert table.lookup(model, on=DAY) is not None, model


def test_lookup_matches_exact_ids_and_unique_bare_names() -> None:
    table = build_table()

    sol = table.lookup("openai/gpt-6.1-sol", on=DAY)
    assert sol is not None and (sol.input, sol.cached_input, sol.output) == (2.0, 0.10, 10.0)
    assert table.lookup("gpt-6.1-sol", on=DAY) == sol  # CrewAI events carry the bare name
    assert table.lookup("OpenAI/GPT-6.1-SOL", on=DAY) == sol
    assert table.lookup("google/gemini-3.8-flash", on=DAY) == table.lookup(
        "gemini/gemini-3.8-flash", on=DAY
    )


def test_an_unknown_model_has_no_price() -> None:
    assert build_table().lookup("openai/gpt-9-imaginary", on=DAY) is None
    assert build_table().lookup("azure/my-deployment", on=DAY) is None


def test_a_bare_name_shared_by_two_providers_is_unknown_not_guessed() -> None:
    table = PriceTable([Price("a/shared", 1, 2), Price("b/shared", 3, 4)])

    assert table.lookup("shared") is None
    found = table.lookup("a/shared")
    assert found is not None and found.input == 1


def test_prices_change_on_their_effective_dates() -> None:
    table = build_table()

    intro = table.lookup("gemini/gemini-3.8-flash", on=date(2026, 12, 31))
    standard = table.lookup("gemini/gemini-3.8-flash", on=date(2027, 1, 1))

    assert intro is not None and (intro.input, intro.output) == (0.75, 3.75)
    assert standard is not None and (standard.input, standard.output) == (1.50, 7.50)


def test_a_price_outside_every_validity_window_is_unknown() -> None:
    table = PriceTable([Price("x/m", 1, 2, valid_until=date(2026, 1, 1))])

    assert table.lookup("x/m", on=date(2026, 6, 1)) is None


def test_overrides_beat_shipped_prices_and_can_add_models() -> None:
    table = build_table(
        {
            "openai/gpt-6-luna": {"input": 0.5, "output": 1.0},
            "acme/custom-1": {"input": 3, "output": 6, "cached_input": 0.3},
        }
    )

    luna = table.lookup("openai/gpt-6-luna", on=DAY)
    custom = table.lookup("acme/custom-1", on=DAY)
    assert luna is not None and (luna.input, luna.output, luna.overridden) == (0.5, 1.0, True)
    assert luna.cached_input is None  # an override replaces the whole row
    assert custom is not None and custom.cached_input == 0.3
    assert table.lookup("openai/gpt-6.1-sol", on=DAY).input == 2.0  # type: ignore[union-attr]


def test_a_provider_wildcard_override_covers_its_models() -> None:
    table = build_table({"ollama/*": {"input": 0, "output": 0}})

    price = table.lookup("ollama/llama9:70b", on=DAY)

    assert price is not None and price.is_free
    assert table.lookup("openai/other", on=DAY) is None


def test_normalize_model_resolves_provider_aliases() -> None:
    assert normalize_model(" Google/Gemini-3.8-Flash ") == "gemini/gemini-3.8-flash"
    assert normalize_model("gpt-6-luna") == "gpt-6-luna"


# -- cost arithmetic ------------------------------------------------------------------------


def test_cost_prices_fresh_cached_cache_write_and_output_tokens_separately() -> None:
    price = Price("p/m", input=2.0, output=10.0, cached_input=0.2, cache_write=2.5)

    cost = estimate_cost(
        price,
        prompt_tokens=1_000_000,
        completion_tokens=100_000,
        cached_prompt_tokens=600_000,
        cache_creation_tokens=100_000,
    )

    # 300k fresh x 2 + 600k cached x 0.2 + 100k written x 2.5 + 100k output x 10
    assert cost == pytest.approx(0.6 + 0.12 + 0.25 + 1.0)


def test_cost_falls_back_to_the_input_rate_when_a_cache_price_is_missing() -> None:
    price = Price("p/m", input=2.0, output=10.0)

    cost = estimate_cost(
        price, prompt_tokens=1_000_000, completion_tokens=0, cached_prompt_tokens=500_000
    )

    assert cost == pytest.approx(2.0)


def test_cost_never_counts_more_cached_tokens_than_the_prompt_holds() -> None:
    price = Price("p/m", input=1.0, output=0.0, cached_input=0.0, cache_write=1.0)

    cost = estimate_cost(
        price, prompt_tokens=1_000_000, completion_tokens=0, cached_prompt_tokens=5_000_000
    )

    assert cost == 0.0


def test_an_unknown_price_gives_no_cost_and_a_free_price_gives_zero() -> None:
    assert estimate_cost(None, prompt_tokens=10, completion_tokens=10) is None
    free = build_table().lookup("ollama/qwen3.8:27b", on=DAY)
    assert estimate_cost(free, prompt_tokens=10_000, completion_tokens=5_000) == 0.0


def test_format_price() -> None:
    assert format_price(Price("p/m", 2.0, 10.0)) == "$2/$10 per MTok"
    assert format_price(Price("p/m", 0, 0)) == "free"


# -- configuration --------------------------------------------------------------------------


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "engineering-team.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_price_overrides_come_from_the_config_file(tmp_path: Path) -> None:
    settings = load_settings(
        config_file=str(
            _config(tmp_path, '[pricing."openai/gpt-6.1-sol"]\ninput = 1.5\noutput = 7\n')
        )
    )

    price = settings.price_table().lookup("openai/gpt-6.1-sol", on=DAY)

    assert price is not None and (price.input, price.output) == (1.5, 7.0)
    rows = {row.key: row for row in settings.describe()}
    assert rows["pricing.openai/gpt-6.1-sol"].value == "$1.5/$7 per MTok (override)"
    assert "$1.5/$7 per MTok" in rows["model.lead"].value


def test_config_show_marks_models_without_a_price(tmp_path: Path) -> None:
    body = (
        'enable_azure = true\nprovider = "azure"\n'
        '[models.tiers.lead]\nmodel = "azure/my-gpt"\n'
        '[models.tiers.worker]\nmodel = "azure/my-gpt"\n'
    )
    settings = load_settings(config_file=str(_config(tmp_path, body)))

    rows = {row.key: row.value for row in settings.describe()}

    assert "price unknown" in rows["model.lead"]


def test_invalid_price_overrides_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="provider/model-id"):
        load_settings(
            config_file=str(_config(tmp_path, "[pricing.nomodel]\ninput = 1\noutput = 2\n"))
        )
    with pytest.raises(SettingsError):
        load_settings(
            config_file=str(_config(tmp_path, '[pricing."a/b"]\ninput = -1\noutput = 2\n'))
        )
    with pytest.raises(SettingsError):
        load_settings(config_file=str(_config(tmp_path, '[pricing."a/b"]\ninput = 1\n')))
