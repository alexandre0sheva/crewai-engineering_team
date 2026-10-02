"""``[web]`` and ``[knowledge]`` settings: off by default, keys by environment name only."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team import main
from engineering_team.settings import Settings, SettingsError, load_settings

SECRET = "sk-serper-0123456789abcdef"


def load(tmp_path: Path, env: dict[str, str] | None = None, **kwargs: object) -> Settings:
    return load_settings(env=env or {}, cwd=tmp_path, home=tmp_path / "home", **kwargs)  # type: ignore[arg-type]


def test_the_web_tools_are_off_by_default(tmp_path: Path) -> None:
    settings = load(tmp_path)

    assert settings.web.enabled is False
    assert settings.web.search_provider is None
    assert settings.web.roles == []
    assert settings.knowledge.context_dirs == []


def test_the_environment_and_the_cli_flag_enable_the_web_tools(tmp_path: Path) -> None:
    from_env = load(tmp_path, {"ENGINEERING_ALLOW_WEB": "true"})
    args = main._run_parser().parse_args(["--allow-web"])
    from_cli = load(tmp_path, overrides=main._cli_overrides(args))

    assert from_env.web.enabled and from_env.source_of("web.enabled") == "env ENGINEERING_ALLOW_WEB"
    assert from_cli.web.enabled and from_cli.source_of("web.enabled") == "cli"
    assert main._cli_overrides(main._run_parser().parse_args([])).get("web.enabled") is None


def test_the_config_file_sets_the_web_section(tmp_path: Path) -> None:
    (tmp_path / "engineering-team.toml").write_text(
        '[web]\nenabled = true\nsearch_provider = "brave"\nroles = ["Researcher"]\n'
        'allow_domains = ["Docs.Python.org", "*.example.com"]\ndeny_domains = ["bad.example.com"]\n'
        "max_requests_per_run = 5\n[knowledge]\ncontext_dirs = ['notes']\n",
        encoding="utf-8",
    )

    settings = load(tmp_path)

    assert settings.web.search_provider == "brave"
    assert settings.web.roles == ["researcher"]
    assert settings.web.allow_domains == ["docs.python.org", "*.example.com"]
    assert settings.web.max_requests_per_run == 5
    assert settings.knowledge.context_dirs == ["notes"]


@pytest.mark.parametrize(
    ("key", "value"),
    [("search_provider", "bing"), ("max_requests_per_run", 0), ("timeout_seconds", 999)],
)
def test_invalid_web_values_are_rejected_with_the_key_name(
    tmp_path: Path, key: str, value: object
) -> None:
    with pytest.raises(SettingsError, match=f"web.{key}"):
        load(tmp_path, overrides={f"web.{key}": value})


def test_search_keys_come_from_the_environment_by_name_and_are_never_shown(
    tmp_path: Path,
) -> None:
    settings = load(tmp_path, {"SERPER_API_KEY": SECRET, "OPENAI_API_KEY": "x"}).with_overrides(
        {"web.enabled": True}, source="test"
    )

    assert settings.web_api_key("serper") == SECRET  # survives with_overrides
    assert settings.web_api_key("brave") is None
    shown = (
        repr(settings)
        + str(settings.model_dump())
        + "\n".join(f"{row.key} {row.value}" for row in settings.describe())
    )
    assert SECRET not in shown
    assert "credentials.SERPER_API_KEY" in shown


def test_enabled_web_without_any_search_key_is_reported_as_missing(tmp_path: Path) -> None:
    settings = load(tmp_path, overrides={"web.enabled": True})

    rows = {row.key: row.value for row in settings.describe()}

    assert rows["credentials.SERPER_API_KEY/BRAVE_API_KEY/TAVILY_API_KEY"] == "MISSING"
