"""OpenRouter: provider resolution, routing settings, its key, and its reported cost."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering_team.contracts import Event
from engineering_team.model_routing import PREFIX_TO_PROVIDER, TIER_NAMES, provider_prefix
from engineering_team.pricing import build_table
from engineering_team.runtime.bridge import _usage, _with_provider
from engineering_team.runtime.usage import UsageTracker
from engineering_team.settings import Settings, SettingsError, load_settings

NOW = datetime(2026, 11, 1, tzinfo=UTC)
KEYS = {"OPENROUTER_API_KEY": "or-secret-value"}


def load(tmp_path: Path, env: dict[str, str] | None = None, **overrides: object) -> Settings:
    return load_settings(overrides=overrides, env=env or {}, cwd=tmp_path, home=tmp_path)


def test_the_openrouter_preset_resolves_to_openrouter_models_with_their_context(
    tmp_path: Path,
) -> None:
    settings = load(tmp_path, KEYS, provider="openrouter")

    for tier in TIER_NAMES:
        resolved = settings.resolve_model("backend_engineer", tier=tier)
        assert resolved.model.startswith("openrouter/") and resolved.model.count("/") == 2
        assert resolved.provider == "openrouter"
        assert resolved.context_window and resolved.context_window >= 200_000
        assert resolved.api is None  # Chat Completions: OpenRouter's tool calls
        assert build_table().lookup(resolved.model) is not None, resolved.model


def test_the_openrouter_key_is_checked_by_name(tmp_path: Path) -> None:
    missing = load(tmp_path, provider="openrouter").missing_credentials()
    present = load(tmp_path, KEYS, provider="openrouter").missing_credentials()

    assert missing == ["OPENROUTER_API_KEY is not set (needed by openrouter models)"]
    assert present == []


def test_routing_defaults_deny_data_collection_and_need_tool_support(tmp_path: Path) -> None:
    routing = load(tmp_path).openrouter.provider_routing()

    assert routing["data_collection"] == "deny"
    assert routing["require_parameters"] is True
    assert routing["allow_fallbacks"] is True
    assert "fp4" not in routing["quantizations"] and "fp8" in routing["quantizations"]
    assert load(tmp_path).openrouter.headers() == {}  # attribution is off by default


def test_routing_settings_come_from_the_config_and_base_url_from_the_environment(
    tmp_path: Path,
) -> None:
    (tmp_path / "engineering-team.toml").write_text(
        "[openrouter]\n"
        'order = ["deepinfra", "z-ai"]\n'
        "allow_fallbacks = false\n"
        'quantizations = ["bf16"]\n'
        'sort = "throughput"\n'
        'app_url = "https://example.org/app"\n'
        'app_title = "My app"\n',
        encoding="utf-8",
    )
    settings = load(tmp_path, {"OPENROUTER_BASE_URL": "https://proxy.example.org/v1"})

    assert settings.openrouter.provider_routing() == {
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "order": ["deepinfra", "z-ai"],
        "quantizations": ["bf16"],
        "sort": "throughput",
    }
    assert settings.openrouter.headers() == {
        "HTTP-Referer": "https://example.org/app",
        "X-OpenRouter-Title": "My app",
    }
    assert settings.openrouter.base_url == "https://proxy.example.org/v1"
    assert settings.source_of("openrouter.base_url") == "env OPENROUTER_BASE_URL"


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('quantizations = ["fp3"]', "quantizations"),
        ('order = ["Not A Slug"]', "host slugs"),
        ('app_title = "x"', "app_title needs openrouter.app_url"),
        ('base_url = "openrouter.ai"', "http"),
        ('data_collection = "maybe"', "data_collection"),
    ],
)
def test_invalid_routing_settings_fail_in_one_line(
    tmp_path: Path, table: str, message: str
) -> None:
    (tmp_path / "engineering-team.toml").write_text(f"[openrouter]\n{table}\n", encoding="utf-8")

    with pytest.raises(SettingsError, match=message):
        load(tmp_path)


def test_build_llm_sends_routing_and_replaces_crewais_attribution_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.llm import build_llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings = load(tmp_path, KEYS, provider="openrouter")
    llm = build_llm(settings.resolve_model("backend_engineer"), settings)

    assert type(llm).__name__ == "OpenAICompatibleCompletion"
    assert llm.additional_params["extra_body"] == {
        "provider": settings.openrouter.provider_routing()
    }
    client = llm._get_client_params()
    assert client["default_headers"] == {}  # not CrewAI's "HTTP-Referer: https://crewai.com"
    assert client["base_url"] == "https://openrouter.ai/api/v1"
    assert "reasoning_effort" not in llm.additional_params


def test_the_call_usage_carries_openrouters_reported_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.llm import build_llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings = load(tmp_path, KEYS, provider="openrouter")
    llm = build_llm(settings.resolve_model("backend_engineer"), settings)
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=50,
        total_tokens=1050,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        completion_tokens_details=None,
        cost=0.00042,
    )

    extracted = llm._extract_openai_token_usage(SimpleNamespace(usage=usage))
    no_cost = llm._extract_openai_token_usage(
        SimpleNamespace(usage=SimpleNamespace(**{**vars(usage), "cost": None}))
    )

    assert extracted["cost"] == 0.00042 and extracted["cached_prompt_tokens"] == 600
    assert "cost" not in no_cost
    assert _usage(extracted)["cost_usd"] == 0.00042
    assert "cost_usd" not in _usage(no_cost)


MODEL = "openrouter/z-ai/glm-5.3-flash"


def _call(tracker: UsageTracker, cost: float | None, model: str = MODEL) -> None:
    usage: dict[str, float] = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    if cost is not None:
        usage["cost_usd"] = cost
    tracker.emit("llm.call", stage="build", agent="dev", model=model, usage=usage)


def test_reported_costs_are_used_when_every_call_reported_one() -> None:
    table = build_table()
    reported, mixed, unpriced = UsageTracker(), UsageTracker(), UsageTracker()
    for tracker, costs in ((reported, [0.01, 0.02]), (mixed, [0.01, None])):
        for cost in costs:
            _call(tracker, cost)
    _call(unpriced, 0.5, model="openrouter/acme/unlisted")

    row = reported.report(table).rows[0]
    assert (row.cost_usd, row.cost_source) == (pytest.approx(0.03), "provider")
    # A call without a reported cost: the whole row falls back to the price table.
    price = table.lookup(MODEL)
    assert price is not None
    row = mixed.report(table).rows[0]
    assert (row.cost_usd, row.cost_source) == (pytest.approx(2 * price.input), "price table")
    # A model with no price is still costed when OpenRouter says what it billed.
    assert unpriced.report(table).estimated_cost_usd == pytest.approx(0.5)


def test_reported_costs_survive_a_rebuild_from_the_event_log() -> None:
    events = [
        Event(seq=n, ts=NOW, type="llm.call", run_id="r", stage="build", agent="dev",
              data={"model": MODEL, "usage": {"prompt_tokens": 10, "cost_usd": 0.001}})
        for n in range(3)
    ]  # fmt: skip

    report = UsageTracker.from_events(events).report(build_table())

    assert report.estimated_cost_usd == pytest.approx(0.003)


def test_an_aggregators_model_name_keeps_its_own_slash() -> None:
    assert _with_provider("deepseek/deepseek-v4.1-flash", "openrouter") == (
        "openrouter/deepseek/deepseek-v4.1-flash"
    )
    assert _with_provider("openrouter/x/y", "openrouter") == "openrouter/x/y"
    assert _with_provider("gpt-6-luna", "openai") == "openai/gpt-6-luna"
    assert _with_provider("gpt-6-luna", None) == "gpt-6-luna"
    # Another provider's slashed name is already qualified (the scripted test LLM's ollama/...).
    assert _with_provider("ollama/qwen3.8:27b", "scripted") == "ollama/qwen3.8:27b"


# --- the mixed `value` preset ------------------------------------------------------------


def _providers(settings: Settings) -> dict[str, str]:
    return {
        tier: PREFIX_TO_PROVIDER[
            provider_prefix(settings.resolve_model("backend_engineer", tier=tier).model)
        ]
        for tier in TIER_NAMES
    }


@pytest.mark.parametrize("only", ["openai", "anthropic", "google", "openrouter"])
def test_value_falls_back_tier_by_tier_to_the_one_key_present(tmp_path: Path, only: str) -> None:
    names = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    settings = load(tmp_path, {names[only]: "x"}, provider="value")

    assert set(_providers(settings).values()) == {only}
    assert settings.missing_credentials() == []


def test_value_mixes_providers_by_measured_value_when_several_keys_are_set(
    tmp_path: Path,
) -> None:
    every_key = {
        "OPENAI_API_KEY": "a",
        "ANTHROPIC_API_KEY": "b",
        "GOOGLE_API_KEY": "c",
        "OPENROUTER_API_KEY": "d",
    }
    settings = load(tmp_path, every_key, provider="value")
    no_openai = load(tmp_path, {**every_key, "OPENAI_API_KEY": ""}, provider="value")

    assert _providers(settings) == {
        "max": "openai",
        "lead": "openai",
        "reviewer": "openrouter",
        "worker": "openai",
        "cheap": "openai",
    }
    assert _providers(no_openai) == {
        "max": "openrouter",
        "lead": "openrouter",
        "reviewer": "openrouter",
        "worker": "anthropic",
        "cheap": "anthropic",
    }
    source = no_openai.resolve_model("backend_engineer", tier="worker").sources["model"]
    assert source == "value preset (worker, anthropic)"


def test_value_with_no_key_reports_what_is_missing(tmp_path: Path) -> None:
    settings = load(tmp_path, provider="value")

    assert settings.missing_credentials()  # its first choices, named by their variables
    with pytest.raises(SettingsError, match="is not set"):
        settings.check_ready(require_credentials=True)
