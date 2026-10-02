from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from engineering_team.model_routing import PROVIDER_PRESETS, TIER_NAMES
from engineering_team.pricing import build_table, default_prices
from engineering_team.settings import (
    SMOKE_PROFILE_MARKER,
    Settings,
    SettingsError,
    load_settings,
    mask_url,
)

LEAD = "engineering_lead"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


def load(project: Path, home: Path, env: dict[str, str] | None = None, **kwargs) -> Settings:
    """Load with a fully explicit, isolated environment (no process env, no real home)."""

    return load_settings(env=env or {}, cwd=project, home=home, **kwargs)


def write_user_config(home: Path, text: str) -> None:
    path = home / ".config" / "engineering-team" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def write_project_config(project: Path, text: str) -> None:
    (project / "engineering-team.toml").write_text(text, encoding="utf-8")


# --- defaults and presets --------------------------------------------------------------


def test_defaults(project: Path, home: Path) -> None:
    settings = load(project, home)

    assert (settings.provider, settings.profile) == ("openai", "standard")
    assert settings.parallel.max_parallel_agents == 3
    assert settings.execution.backend == "local"
    assert settings.verbose is True and settings.tracing is False
    lead = settings.resolve_model(LEAD)
    worker = settings.resolve_model("backend_engineer")
    assert (lead.model, lead.reasoning_effort, lead.max_iter) == ("openai/gpt-6.1-sol", "high", 35)
    assert (worker.model, worker.reasoning_effort, worker.max_iter) == (
        "openai/gpt-6-luna",
        "low",
        30,
    )
    assert settings.source_of("provider") == "default"


@pytest.mark.parametrize(
    ("provider", "lead", "worker"),
    [
        ("openai", "openai/gpt-6.1-sol", "openai/gpt-6-luna"),
        ("anthropic", "anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
        ("google", "gemini/gemini-3.8-flash", "gemini/gemini-3.8-flash"),
        ("ollama", "ollama/qwen3.8:27b", "ollama/qwen3.8:27b"),
    ],
)
def test_provider_presets(project: Path, home: Path, provider: str, lead: str, worker: str) -> None:
    settings = load(project, home, {"ENGINEERING_PROVIDER": provider})

    assert settings.resolve_model(LEAD).model == lead
    assert settings.resolve_model("quality_engineer").model == worker
    assert settings.source_of("provider") == "env ENGINEERING_PROVIDER"


def test_every_preset_defines_every_tier() -> None:
    for provider, tiers in PROVIDER_PRESETS.items():
        if tiers is not None:
            assert set(tiers) == set(TIER_NAMES), provider


def test_azure_is_disabled_by_default(project: Path, home: Path) -> None:
    with pytest.raises(SettingsError, match="Azure is disabled by default") as error:
        load(project, home, {"ENGINEERING_PROVIDER": "azure"})
    assert "\n" not in str(error.value)
    assert "ENGINEERING_ENABLE_AZURE" in str(error.value)

    # Models with an azure/ prefix are refused too, whatever the provider preset.
    settings = load(project, home, {"ENGINEERING_LEAD_MODEL": "azure/gpt-lead"})
    with pytest.raises(SettingsError, match="Azure is disabled by default"):
        settings.resolve_model(LEAD)


def test_azure_can_be_enabled_and_has_no_default_models(project: Path, home: Path) -> None:
    enabled = {"ENGINEERING_PROVIDER": "azure", "ENGINEERING_ENABLE_AZURE": "true"}

    with pytest.raises(SettingsError, match="deployment names"):
        load(project, home, enabled).resolve_model(LEAD)

    configured = load(
        project,
        home,
        {
            **enabled,
            "ENGINEERING_LEAD_MODEL": "azure/gpt-lead",
            "ENGINEERING_WORKER_MODEL": "azure/gpt-worker",
        },
    )
    assert configured.resolve_model(LEAD).model == "azure/gpt-lead"
    assert configured.resolve_model("backend_engineer").model == "azure/gpt-worker"

    write_project_config(project, 'provider = "azure"\nenable_azure = true\n')
    assert load(project, home).provider == "azure"


def test_presets_contain_only_the_approved_models() -> None:
    models = {tier.model for tiers in PROVIDER_PRESETS.values() if tiers for tier in tiers.values()}

    assert models == {
        "openai/gpt-6.1-sol",
        "openai/gpt-6-luna",
        "anthropic/claude-opus-5-5",
        "anthropic/claude-sonnet-5-5",
        "gemini/gemini-3.8-flash",
        "ollama/qwen3.8:27b",
    }
    assert {price.model for price in default_prices()} == models  # every preset model is priced


PRICE_DAY = date(2026, 10, 2)


def _output_price(model: str) -> float:
    price = build_table().lookup(model, on=PRICE_DAY)
    assert price is not None, model
    return price.output  # local models are priced at zero


def test_tiers_never_get_cheaper_as_they_get_more_capable() -> None:
    """Presets are organised by price: cheap <= worker <= reviewer <= lead <= max."""

    order = ["cheap", "worker", "reviewer", "lead", "max"]
    for provider, tiers in PROVIDER_PRESETS.items():
        if not tiers:
            continue
        prices = [_output_price(tiers[name].model) for name in order]
        assert prices == sorted(prices), (provider, prices)


def test_documented_price_ordering() -> None:
    output_price = _output_price

    luna = output_price("openai/gpt-6-luna")
    flash = output_price("gemini/gemini-3.8-flash")
    sol = output_price("openai/gpt-6.1-sol")
    sonnet = output_price("anthropic/claude-sonnet-5-5")
    opus = output_price("anthropic/claude-opus-5-5")
    assert luna < flash < sol == sonnet < opus


def test_profiles_select_tiers_and_iteration_caps(project: Path, home: Path) -> None:
    smoke = load(project, home, {"ENGINEERING_RUN_PROFILE": "smoke"})
    best = load(project, home, {"ENGINEERING_RUN_PROFILE": "max-quality"})

    smoke_lead, smoke_worker = smoke.resolve_model(LEAD), smoke.resolve_model("backend_engineer")
    assert (smoke_lead.model, smoke_lead.reasoning_effort, smoke_lead.max_iter) == (
        "openai/gpt-6-luna",
        "low",
        18,
    )
    assert (smoke_worker.reasoning_effort, smoke_worker.max_iter) == ("none", 14)
    assert best.resolve_model(LEAD).model == "openai/gpt-6.1-sol"
    assert best.resolve_model(LEAD).reasoning_effort == "xhigh"
    assert best.resolve_model(LEAD).max_iter == 50
    assert (
        load(
            project,
            home,
            {"ENGINEERING_RUN_PROFILE": "max-quality", "ENGINEERING_PROVIDER": "anthropic"},
        )
        .resolve_model(LEAD)
        .model
        == "anthropic/claude-opus-5-5"
    )
    assert smoke.docs_mcp_enabled is False and best.docs_mcp_enabled is True


def test_gpt6_models_get_responses_api_and_a_real_context_window(project: Path, home: Path) -> None:
    resolved = load(project, home).resolve_model(LEAD)

    assert resolved.api == "responses"
    assert resolved.context_window == 1_050_000
    assert resolved.sources["api"] == "model facts"


def test_overriding_to_another_provider_drops_model_specific_facts(
    project: Path, home: Path
) -> None:
    settings = load(project, home, {"ENGINEERING_LEAD_MODEL": "anthropic/claude-opus-5-5"})

    resolved = settings.resolve_model(LEAD)

    assert resolved.model == "anthropic/claude-opus-5-5"
    assert resolved.api is None and resolved.context_window is None


# --- precedence ------------------------------------------------------------------------

KEY = "parallel.max_parallel_agents"


def test_full_precedence_chain(project: Path, home: Path) -> None:
    write_user_config(home, "[parallel]\nmax_parallel_agents = 4\n")
    assert load(project, home).parallel.max_parallel_agents == 4
    assert load(project, home).source_of(KEY).startswith("user config")

    write_project_config(project, "[parallel]\nmax_parallel_agents = 5\n")
    settings = load(project, home)
    assert settings.parallel.max_parallel_agents == 5
    assert settings.source_of(KEY).startswith("project config")

    env = {"ENGINEERING_MAX_PARALLEL": "6"}
    settings = load(project, home, env)
    assert settings.parallel.max_parallel_agents == 6
    assert settings.source_of(KEY) == "env ENGINEERING_MAX_PARALLEL"

    settings = load(project, home, env, overrides={KEY: 7})
    assert settings.parallel.max_parallel_agents == 7
    assert settings.source_of(KEY) == "cli"


def test_nested_tables_merge_across_layers(project: Path, home: Path) -> None:
    write_user_config(home, "[parallel]\nmax_rpm = 30\n")
    write_project_config(project, "[parallel]\nmax_parallel_agents = 2\n")

    settings = load(project, home)

    assert (settings.parallel.max_parallel_agents, settings.parallel.max_rpm) == (2, 30)


def test_blank_environment_values_count_as_unset(project: Path, home: Path) -> None:
    write_project_config(project, 'provider = "ollama"\n')

    settings = load(project, home, {"ENGINEERING_PROVIDER": "   ", "ENGINEERING_MAX_RPM": ""})

    assert settings.provider == "ollama"
    assert settings.parallel.max_rpm is None


def test_explicit_config_file_replaces_the_project_file(
    project: Path, home: Path, tmp_path: Path
) -> None:
    write_project_config(project, 'provider = "ollama"\n')
    other = tmp_path / "other.toml"
    other.write_text('provider = "google"\n', encoding="utf-8")

    assert load(project, home, config_file=other).provider == "google"
    assert load(project, home, {"ENGINEERING_CONFIG_FILE": str(other)}).provider == "google"


def test_missing_explicit_config_file_is_an_error(project: Path, home: Path) -> None:
    with pytest.raises(SettingsError, match="Config file not found"):
        load(project, home, config_file="nope.toml")


# --- 0.1.0 environment names -----------------------------------------------------------


def test_zero_one_zero_environment_names_still_work(project: Path, home: Path) -> None:
    env = {
        "ENGINEERING_LEAD_MODEL": "openai/legacy-lead",
        "ENGINEERING_LEAD_REASONING_EFFORT": "medium",
        "ENGINEERING_LEAD_MAX_ITER": "11",
        "ENGINEERING_WORKER_MODEL": "openai/legacy-worker",
        "ENGINEERING_WORKER_REASONING_EFFORT": "none",
        "ENGINEERING_WORKER_MAX_ITER": "9",
        "ENGINEERING_VERBOSE": "no",
        "ENGINEERING_TRACING": "1",
        "ENGINEERING_DOCS_MCP_URLS": "https://a.example/mcp, https://b.example/mcp",
        "ENGINEERING_COMMAND_ALLOWLIST": "just, Flutter",
        "ENGINEERING_SUBPROCESS_ENV_ALLOWLIST": "DATABASE_URL",
        "ENGINEERING_PROJECT_NAME": "legacy-app",
        "ENGINEERING_WORKSPACE_ROOT": "out",
        "ENGINEERING_PROJECT_REQUEST": "Build it",
        "ENGINEERING_REQUEST_FILE": "req.md",
    }

    settings = load(project, home, env)

    lead = settings.resolve_model(LEAD)
    worker = settings.resolve_model("backend_engineer")
    assert (lead.model, lead.reasoning_effort, lead.max_iter) == (
        "openai/legacy-lead",
        "medium",
        11,
    )
    assert (worker.model, worker.reasoning_effort, worker.max_iter) == (
        "openai/legacy-worker",
        "none",
        9,
    )
    assert lead.context_window is None and lead.api is None  # unknown model: no preset facts leak
    assert settings.verbose is False and settings.tracing is True
    assert settings.docs_mcp_urls == ["https://a.example/mcp", "https://b.example/mcp"]
    assert settings.command_allowlist == ["just", "Flutter"]
    assert settings.subprocess_env_allowlist == ["DATABASE_URL"]
    assert (settings.project_name, settings.workspace_root) == ("legacy-app", "out")
    assert (settings.request, settings.request_file) == ("Build it", "req.md")


def test_standard_variables_do_not_leak_into_smoke_mode(project: Path, home: Path) -> None:
    """0.1.0 guarantee: production settings cannot make the cheap smoke run expensive."""

    env = {
        "ENGINEERING_RUN_PROFILE": "smoke",
        "ENGINEERING_LEAD_MODEL": "openai/gpt-6.1-sol",
        "ENGINEERING_WORKER_MAX_ITER": "99",
        "ENGINEERING_SMOKE_WORKER_MODEL": "openai/gpt-6-luna",
        "ENGINEERING_SMOKE_LEAD_MAX_ITER": "5",
    }

    settings = load(project, home, env)

    assert settings.resolve_model(LEAD).model == "openai/gpt-6-luna"
    assert settings.resolve_model(LEAD).max_iter == 5
    assert settings.resolve_model("backend_engineer").max_iter == 14


# --- smoke marker ----------------------------------------------------------------------


def test_smoke_marker_selects_smoke_only_when_nothing_else_chose_a_profile(
    project: Path, home: Path
) -> None:
    request = f"{SMOKE_PROFILE_MARKER}\n# Tiny"

    marked = load(project, home).for_request(request)
    assert marked.profile == "smoke"
    assert marked.source_of("profile") == "request marker"

    assert (
        load(project, home, {"ENGINEERING_RUN_PROFILE": "standard"}).for_request(request).profile
        == "standard"
    )
    assert (
        load(project, home, overrides={"profile": "max-quality"}).for_request(request).profile
        == "max-quality"
    )
    write_project_config(project, 'profile = "standard"\n')
    assert load(project, home).for_request(request).profile == "standard"
    assert load(project, home).for_request("# no marker").profile == "standard"


# --- per-role and tier overrides --------------------------------------------------------


def test_per_role_override_string_and_table_forms(project: Path, home: Path) -> None:
    write_project_config(
        project,
        """
[models.roles]
quality_engineer = "openai/gpt-6.1-sol"

[models.roles.backend_engineer]
model = "openai/gpt-6.1-sol"
reasoning_effort = "xhigh"
temperature = 0.2
max_iter = 12
""",
    )

    settings = load(project, home)

    quality = settings.resolve_model("quality_engineer")
    backend = settings.resolve_model("backend_engineer")
    other = settings.resolve_model("frontend_engineer")
    assert quality.model == "openai/gpt-6.1-sol" and quality.reasoning_effort == "low"
    assert (backend.model, backend.reasoning_effort, backend.temperature, backend.max_iter) == (
        "openai/gpt-6.1-sol",
        "xhigh",
        0.2,
        12,
    )
    assert other.model == "openai/gpt-6-luna"
    assert quality.sources["model"].startswith("project config")


def test_tier_override_applies_to_every_role_using_that_tier(project: Path, home: Path) -> None:
    write_project_config(project, '[models.tiers.worker]\nmodel = "ollama/llama3.3"\n')

    settings = load(project, home)

    assert settings.resolve_model("backend_engineer").model == "ollama/llama3.3"
    assert settings.resolve_model(LEAD).model == "openai/gpt-6.1-sol"


def test_role_override_beats_profile_and_tier_overrides(project: Path, home: Path) -> None:
    write_project_config(
        project,
        """
[models.tiers.worker]
model = "openai/gpt-6-luna"
[profiles.standard.worker]
model = "openai/gpt-6.1-sol"
[models.roles]
backend_engineer = "openai/gpt-6.1-sol"
""",
    )

    settings = load(project, home)

    assert settings.resolve_model("backend_engineer").model == "openai/gpt-6.1-sol"
    assert settings.resolve_model("frontend_engineer").model == "openai/gpt-6.1-sol"


# --- validation: one-line errors ---------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"ENGINEERING_PROVIDER": "skynet"}, "ENGINEERING_PROVIDER"),
        ({"ENGINEERING_RUN_PROFILE": "turbo"}, "ENGINEERING_RUN_PROFILE"),
        ({"ENGINEERING_MAX_PARALLEL": "many"}, "ENGINEERING_MAX_PARALLEL"),
        ({"ENGINEERING_MAX_PARALLEL": "0"}, "greater than or equal to 1"),
        ({"ENGINEERING_LEAD_REASONING_EFFORT": "extreme"}, "ENGINEERING_LEAD_REASONING_EFFORT"),
        ({"ENGINEERING_LEAD_MODEL": "gpt-6"}, "provider/model-id"),
        ({"ENGINEERING_VERBOSE": "maybe"}, "true/false"),
        ({"ENGINEERING_BUDGET_MAX_COST_USD": "-1"}, "budget.max_cost_usd"),
        ({"ENGINEERING_EXECUTION_BACKEND": "vm"}, "execution.backend"),
    ],
)
def test_invalid_environment_values_give_one_actionable_line(
    project: Path, home: Path, env: dict[str, str], needle: str
) -> None:
    with pytest.raises(SettingsError) as error:
        load(project, home, env)

    assert "\n" not in str(error.value)
    assert needle in str(error.value)


def test_config_file_errors_name_the_file_and_suggest_fixes(project: Path, home: Path) -> None:
    write_project_config(project, '[modles.roles]\nx = "openai/gpt-6-luna"\n')

    with pytest.raises(SettingsError) as error:
        load(project, home)

    assert "Unknown setting 'modles'" in str(error.value)
    assert "Did you mean 'models'?" in str(error.value)
    assert "engineering-team.toml" in str(error.value)


def test_invalid_toml_and_unknown_tier_are_reported(project: Path, home: Path) -> None:
    write_project_config(project, "provider = ")
    with pytest.raises(SettingsError, match="not valid TOML"):
        load(project, home)

    write_project_config(project, '[models.tiers.medium]\nmodel = "openai/gpt-6-luna"\n')
    with pytest.raises(SettingsError, match="unknown tier"):
        load(project, home)


def test_invalid_cli_override_is_attributed_to_the_cli(project: Path, home: Path) -> None:
    with pytest.raises(SettingsError, match=r"\(from cli\)"):
        load(project, home, overrides={"provider": "skynet"})


# --- secrets ---------------------------------------------------------------------------


def test_describe_masks_secrets_and_url_credentials(project: Path, home: Path) -> None:
    env = {
        "OPENAI_API_KEY": "sk-super-secret-value",
        "ENGINEERING_DOCS_MCP_URLS": "https://user:hunter2@docs.example/mcp?token=abc123",
        "ENGINEERING_PROJECT_REQUEST": "Build something with the password hunter2 inside",
    }

    text = "\n".join(f"{r.key} {r.value} {r.source}" for r in load(project, home, env).describe())

    assert "sk-super-secret-value" not in text
    assert "hunter2" not in text.replace("password hunter2 inside", "")
    assert "abc123" not in text
    assert "docs.example/mcp" in text
    assert "credentials.OPENAI_API_KEY" in text and "set" in text


def test_describe_reports_missing_credentials_without_values(project: Path, home: Path) -> None:
    rows = {
        row.key: row
        for row in load(project, home, {"ENGINEERING_PROVIDER": "anthropic"}).describe()
    }

    assert rows["credentials.ANTHROPIC_API_KEY"].value == "MISSING"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://u:p@h.example/mcp?token=x#frag", "https://***@h.example/mcp?***"),
        ("https://h.example/mcp", "https://h.example/mcp"),
        ("not a url", "not a url"),
    ],
)
def test_mask_url(url: str, expected: str) -> None:
    assert mask_url(url) == expected


# --- readiness checks ------------------------------------------------------------------


def test_missing_credentials_are_reported_once_per_provider(project: Path, home: Path) -> None:
    settings = load(project, home)

    with pytest.raises(SettingsError, match="OPENAI_API_KEY is not set") as error:
        settings.check_ready(require_credentials=True)
    assert "\n" not in str(error.value)
    settings.check_ready(require_credentials=False)  # preparation needs no credentials

    load(project, home, {"OPENAI_API_KEY": "x"}).check_ready(require_credentials=True)
    load(project, home, {"ENGINEERING_PROVIDER": "ollama"}).check_ready(require_credentials=True)
    load(
        project, home, {"ENGINEERING_PROVIDER": "google", "GEMINI_API_KEY": "x"}
    ).missing_credentials()


def test_mixed_providers_are_all_checked(project: Path, home: Path) -> None:
    settings = load(project, home, {"ENGINEERING_LEAD_MODEL": "anthropic/claude-opus-5-5"})

    assert settings.used_providers() == {"openai", "anthropic"}
    assert len(settings.missing_credentials()) == 2


def test_missing_provider_sdk_names_the_extra(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "anthropic" else real_find_spec(name, *a, **k),
    )
    settings = load(project, home, {"ENGINEERING_PROVIDER": "anthropic"})

    problems = settings.sdk_problems()

    assert len(problems) == 1 and "uv sync --extra anthropic" in problems[0]
    with pytest.raises(SettingsError, match="uv sync --extra anthropic"):
        settings.check_ready(require_credentials=False)
