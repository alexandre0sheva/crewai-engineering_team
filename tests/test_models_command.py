"""``models``: presets, resolved tiers, prices and key presence; ``--refresh-prices`` offline."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from engineering_team import price_refresh
from engineering_team.cli.app import app
from engineering_team.model_routing import PROVIDERS, TIER_NAMES
from engineering_team.price_refresh import compare, median_endpoint_prices, openrouter_id
from engineering_team.pricing import Price
from engineering_team.settings.openrouter import FULL_PRECISION

FIXTURES = Path(__file__).parent / "fixtures" / "openrouter"
SECRET = "sk-or-very-secret-value"


def fixture_fetch(url: str) -> Any:
    if url.endswith("/models"):
        return json.loads((FIXTURES / "models.json").read_text("utf-8"))
    if url.endswith("/z-ai/glm-5.3-flash/endpoints"):
        return json.loads((FIXTURES / "glm-5.3-flash-endpoints.json").read_text("utf-8"))
    if url.endswith("/endpoints"):
        raise OSError("HTTP Error 404: Not Found")  # not in the trimmed fixture
    raise AssertionError(f"unexpected fetch {url}")


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    return tmp_path


def test_models_lists_every_preset_tier_with_prices_and_key_presence(isolated: Path) -> None:
    result = CliRunner().invoke(app, ["--json", "models"])

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    data = json.loads(result.output)
    assert data["active"] == "openai"
    assert data["keys"] == {
        "openai": False,
        "anthropic": False,
        "google": False,
        "openrouter": True,
    }
    presets = {row["preset"] for row in data["presets"]}
    assert presets == set(PROVIDERS) - {"azure"}  # Azure only when enabled
    for preset in presets:
        tiers = [row["tier"] for row in data["presets"] if row["preset"] == preset]
        assert tiers == list(TIER_NAMES)
    rows = {(row["preset"], row["tier"]): row for row in data["presets"]}
    assert rows[("openrouter", "worker")]["key"] == "set"
    assert rows[("openai", "worker")]["key"] == "missing"
    assert rows[("ollama", "worker")]["key"] == "-"
    assert all(row["price"] != "unknown" for row in data["presets"])
    assert rows[("value", "worker")]["model"].startswith("openrouter/")  # the key it has
    assert data["price_changes"] is None  # nothing fetched without --refresh-prices


def test_models_text_output_never_prints_key_values(isolated: Path) -> None:
    result = CliRunner().invoke(app, ["models"])

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    assert "openrouter set" in result.output and "openai missing" in result.output


def test_refresh_prices_prints_differences_and_rewrites_nothing(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(price_refresh, "fetch_json", fixture_fetch)
    shipped = (Path(price_refresh.__file__).parent / "data" / "pricing.toml").read_bytes()

    result = CliRunner().invoke(app, ["--json", "models", "--refresh-prices"])

    assert result.exit_code == 0, result.output
    changes = json.loads(result.output)["price_changes"]
    # The fixture moved luna's output price; the other listed rows match.
    assert {"model": "openai/gpt-6-luna", "field": "output", "table": 0.5, "found": 0.6,
            "source": "https://openrouter.ai/api/v1/models"} in [
        {**c, "found": round(c["found"], 6)} for c in changes if c["found"] is not None
    ]  # fmt: skip
    assert not [c for c in changes if c["model"] == "anthropic/claude-sonnet-5-5"]
    assert not [c for c in changes if c["model"] == "openrouter/z-ai/glm-5.3-flash"]
    unlisted = {c["model"] for c in changes if c["field"] == "listing"}
    assert "anthropic/claude-opus-5-5" in unlisted  # not in the trimmed fixture
    assert (Path(price_refresh.__file__).parent / "data" / "pricing.toml").read_bytes() == shipped


def test_refresh_prices_reports_a_fetch_failure_in_one_line(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(url: str) -> Any:
        raise OSError("network is unreachable")

    monkeypatch.setattr(price_refresh, "fetch_json", offline)

    result = CliRunner().invoke(app, ["models", "--refresh-prices"])

    assert result.exit_code != 0
    assert "Could not fetch OpenRouter's models API: network is unreachable" in result.output


def test_openrouter_ids_of_table_models() -> None:
    assert openrouter_id("anthropic/claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"
    assert openrouter_id("gemini/gemini-3.8-flash") == "google/gemini-3.8-flash"
    assert openrouter_id("openai/gpt-6.1-sol") == "openai/gpt-6.1-sol"
    assert openrouter_id("openrouter/z-ai/glm-5.3") == "z-ai/glm-5.3"


def test_the_endpoint_median_uses_only_allowed_hosts_with_tools() -> None:
    endpoints = [
        {"quantization": "fp4", "pricing": {"prompt": "0.000001", "completion": "0.000001"},
         "supported_parameters": ["tools"]},
        {"quantization": "fp8", "pricing": {"prompt": "0.0000002", "completion": "0.0000004"},
         "supported_parameters": ["tools"]},
        {"quantization": None, "pricing": {"prompt": "0.0000004", "completion": "0.0000008"},
         "supported_parameters": ["tools", "tool_choice"]},
        {"quantization": "bf16", "pricing": {"prompt": "0.000009", "completion": "0.000009"},
         "supported_parameters": ["temperature"]},
    ]  # fmt: skip

    median = median_endpoint_prices(endpoints, FULL_PRECISION)

    assert median == {"input": pytest.approx(0.3), "output": pytest.approx(0.6)}
    assert median_endpoint_prices(endpoints[:1], FULL_PRECISION) is None


def test_compare_skips_free_and_expired_rows() -> None:
    rows = [
        Price("ollama/qwen3.8:27b", 0, 0),
        Price("openai/gpt-6-luna", 0.1, 0.6, valid_until=date(2026, 1, 1)),
    ]

    assert compare(rows, fixture_fetch, quantizations=FULL_PRECISION, on=date(2026, 11, 1)) == []
