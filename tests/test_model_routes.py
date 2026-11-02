"""Per-task model routes and fallback models: settings, resolution and the saved task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engineering_team.model_routes import describe_route, fallback_models, match_route
from engineering_team.pipeline.context_pack import saved_task
from engineering_team.settings import Settings, SettingsError, load_settings


def settings_with(**models: Any) -> Settings:
    return load_settings(overrides={"models": models, "provider": "openai"})


ROUTES = {
    "question/small": {"tier": "cheap"},
    "bugfix/*": {"roles": {"debugger": "reviewer"}},
    "*/large": {"tier": "lead"},
}


def model_of(settings: Settings, role: str, tier: str) -> tuple[str, str]:
    resolved = settings.resolve_model(role, tier=tier)
    return resolved.tier, resolved.model


def test_a_task_without_a_route_keeps_each_teammates_own_tier() -> None:
    settings = settings_with(routes=ROUTES)

    assert model_of(settings, "debugger", "worker")[0] == "worker"  # no task known
    assert model_of(settings.for_task("feature", "small"), "debugger", "worker")[0] == "worker"


def test_a_route_moves_every_teammate_to_its_tier() -> None:
    routed = settings_with(routes=ROUTES).for_task("question", "small")

    assert model_of(routed, "code_reviewer", "reviewer")[0] == "cheap"
    assert model_of(routed, "engineering_lead", "lead")[0] == "cheap"
    assert (
        routed.resolve_model("x", tier="worker").sources["model"].endswith("route question/small")
    )


def test_a_role_in_a_route_wins_over_its_tier_and_others_keep_theirs() -> None:
    routed = settings_with(routes=ROUTES).for_task("bugfix", "large")

    assert model_of(routed, "debugger", "worker")[0] == "reviewer"
    assert model_of(routed, "backend_engineer", "worker")[0] == "worker"


def test_the_most_specific_route_wins() -> None:
    settings = settings_with(routes=ROUTES)

    assert match_route(settings.models.routes, "bugfix", "large").key == "bugfix/*"  # type: ignore[union-attr]
    assert match_route(settings.models.routes, "feature", "large").key == "*/large"  # type: ignore[union-attr]
    assert match_route(settings.models.routes, "question", "small").key == "question/small"  # type: ignore[union-attr]
    assert match_route(settings.models.routes, "feature", "small") is None


def test_exact_route_beats_wildcards_whatever_the_order_of_the_table() -> None:
    routes = {"*/*": {"tier": "max"}, "*/small": {"tier": "worker"}, "docs/*": {"tier": "cheap"}}
    settings = settings_with(routes=routes)

    assert match_route(settings.models.routes, "docs", "small").key == "docs/*"  # type: ignore[union-attr]
    assert match_route(settings.models.routes, "tests", "small").key == "*/small"  # type: ignore[union-attr]
    assert match_route(settings.models.routes, "tests", "large").key == "*/*"  # type: ignore[union-attr]


def test_profiles_that_fix_their_models_ignore_routes() -> None:
    settings = load_settings(
        overrides={"models": {"routes": {"*/*": {"tier": "max"}}}, "profile": "smoke"}
    )

    routed = settings.for_task("bugfix", "large")

    assert routed.resolve_model("debugger", tier="worker") == settings.resolve_model(
        "debugger", tier="worker"
    )


def test_tier_overrides_and_role_overrides_still_apply_after_a_route() -> None:
    settings = settings_with(
        routes={"*/*": {"tier": "reviewer"}},
        tiers={"reviewer": {"model": "openai/gpt-6-luna"}},
        roles={"debugger": {"model": "anthropic/claude-haiku-5-5"}},
    ).for_task("bugfix", "small")

    assert model_of(settings, "backend_engineer", "worker") == ("reviewer", "openai/gpt-6-luna")
    assert model_of(settings, "debugger", "worker")[1] == "anthropic/claude-haiku-5-5"


def test_the_task_survives_more_overrides() -> None:
    settings = settings_with(routes=ROUTES).for_task("question", "small")

    again = settings.with_overrides({"verbose": False}, source="test")

    assert model_of(again, "debugger", "worker")[0] == "cheap"


def test_describe_route_names_what_changes() -> None:
    settings = settings_with(routes=ROUTES)

    assert describe_route(match_route(settings.models.routes, "question", "small")) == (  # type: ignore[arg-type]
        "question/small: all → cheap"
    )
    assert describe_route(match_route(settings.models.routes, "bugfix", "small")) == (  # type: ignore[arg-type]
        "bugfix/*: debugger → reviewer"
    )


@pytest.mark.parametrize(
    "routes",
    [
        {"question": {"tier": "cheap"}},
        {"nonsense/small": {"tier": "cheap"}},
        {"question/huge": {"tier": "cheap"}},
        {"question/small": {"tier": "gold"}},
        {"question/small": {"roles": {"debugger": "gold"}}},
    ],
)
def test_an_invalid_route_is_rejected_with_the_way_out(routes: dict[str, Any]) -> None:
    with pytest.raises(SettingsError) as error:
        settings_with(routes=routes)

    assert "models.routes" in str(error.value)


def test_fallbacks_take_models_or_a_providers_preset_for_the_tier() -> None:
    settings = settings_with(
        fallbacks={"worker": ["anthropic", "openrouter/z-ai/glm-5.3", "openai/gpt-6-luna"]}
    )
    resolved = settings.resolve_model("backend_engineer", tier="worker")

    models = [item.model for item in settings.fallbacks_for(resolved)]

    # The same model as the primary is not a fallback; a provider name is its preset model.
    assert models == ["anthropic/claude-haiku-5-5", "openrouter/z-ai/glm-5.3"]
    assert settings.fallbacks_for(settings.resolve_model("x", tier="max")) == []


def test_a_single_fallback_may_be_a_string() -> None:
    settings = settings_with(fallbacks={"cheap": "google"})

    assert settings.models.fallbacks == {"cheap": ["google"]}


@pytest.mark.parametrize(
    "fallbacks",
    [
        {"gold": ["google"]},
        {"worker": ["not a model"]},
        {"worker": ["value"]},
        {"worker": ["google", "anthropic", "openrouter", "ollama"]},
    ],
)
def test_an_invalid_fallback_is_rejected(fallbacks: dict[str, Any]) -> None:
    with pytest.raises(SettingsError) as error:
        settings_with(fallbacks=fallbacks)

    assert "models.fallbacks" in str(error.value)


def test_the_value_preset_and_unknown_providers_are_not_fallback_entries() -> None:
    resolved = load_settings().resolve_model("x", tier="worker")

    assert fallback_models(["azure", "value", "nonsense"], resolved, lambda _: True) == []


def test_a_fallback_knows_its_models_facts() -> None:
    resolved = load_settings().resolve_model("x", tier="worker")

    (fallback,) = fallback_models(["openrouter/z-ai/glm-5.3"], resolved, lambda _: True)

    assert fallback.context_window == 1_048_576
    assert fallback.tier == resolved.tier and fallback.role == resolved.role


def test_saved_task_reads_the_plan_do_kept(tmp_path: Path) -> None:
    (tmp_path / "task_profile.json").write_text(
        json.dumps({"profile": {"intents": ["bugfix", "tests"], "size": "large"}}), "utf-8"
    )

    assert saved_task(tmp_path) == ("bugfix", "large")
    assert saved_task(tmp_path / "missing") is None
    (tmp_path / "task_profile.json").write_text("{}", "utf-8")
    assert saved_task(tmp_path) is None
