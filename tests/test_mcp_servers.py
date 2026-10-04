"""MCP servers per teammate: config validation, who gets what, the legacy alias, and warnings."""

from __future__ import annotations

from typing import Any

import pytest
from crewai import Agent
from crewai.mcp import MCPServerHTTP, MCPServerSSE, MCPServerStdio

from engineering_team.extensions.agents import agent_extensions
from engineering_team.extensions.mcp import mcps_for, servers_for, trust_notes
from engineering_team.settings import Settings, SettingsError, load_settings
from engineering_team.team import TeamError, build_roster
from engineering_team.testing import ScriptedLLM

DOCS_URL = "https://docs.example.com/mcp"


def settings_with(**overrides: Any) -> Settings:
    return load_settings(overrides=overrides)


def member(settings: Settings, key: str):  # type: ignore[no-untyped-def]
    return build_roster(settings).get(key)


# -- validation (no network is touched) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ({}, "exactly one of 'url'"),
        ({"url": DOCS_URL, "command": "npx"}, "exactly one of 'url'"),
        ({"url": "ftp://x/mcp"}, "http\\(s\\) address"),
        ({"url": "https:///nohost"}, "http\\(s\\) address"),
        ({"command": "  "}, "must not be empty"),
        ({"command": "npx", "transport": "sse"}, "only applies to a url"),
        ({"url": DOCS_URL, "args": ["-y"]}, "only apply to a command"),
        ({"url": DOCS_URL, "roles": ["Not A Key!"]}, "teammate keys"),
        ({"url": DOCS_URL, "surprise": 1}, "surprise"),
    ],
)
def test_an_mcp_server_must_say_one_clear_way_to_reach_it(
    server: dict[str, Any], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        settings_with(**{"mcp.docs": server})


def test_server_names_become_tool_group_names_so_they_must_be_plain() -> None:
    with pytest.raises(SettingsError, match="MCP server name 'My Docs'"):
        settings_with(**{"mcp.My Docs": {"url": DOCS_URL}})


def test_role_names_are_normalised_like_other_teammate_keys() -> None:
    settings = settings_with(**{"mcp.docs": {"url": DOCS_URL, "roles": ["Backend Engineer"]}})

    assert settings.mcp["docs"].roles == ["backend_engineer"]


def test_credentials_in_an_mcp_url_are_masked_when_the_settings_are_described() -> None:
    settings = settings_with(
        **{"mcp.docs": {"url": "https://user:secret@docs.example.com/mcp?k=1"}}
    )

    shown = {row.key: row.value for row in settings.describe()}

    assert shown["mcp.docs.url"] == "https://***@docs.example.com/mcp?***"


# -- who gets which server -------------------------------------------------------------------------


def test_a_teammate_gets_a_server_through_its_tool_group_or_the_servers_roles() -> None:
    settings = settings_with(
        **{
            "mcp.docs": {"url": DOCS_URL, "roles": ["backend_engineer"]},
            "mcp.tickets": {"url": "https://tickets.example.com/mcp"},
            "team.quality_engineer": {"tool_groups": ["fs_read", "mcp:tickets"]},
        }
    )

    assert servers_for(settings, member(settings, "backend_engineer")) == ["docs"]
    assert servers_for(settings, member(settings, "quality_engineer")) == ["tickets"]
    # product_analyst lists mcp:docs (a group name) and so gets the server called docs too.
    assert servers_for(settings, member(settings, "product_analyst")) == ["docs"]
    assert servers_for(settings, member(settings, "codebase_analyst")) == []


def test_the_servers_are_crewai_configurations_with_the_tool_filter_applied() -> None:
    settings = settings_with(
        **{
            "mcp.docs": {"url": DOCS_URL, "allow_tools": ["search_docs"]},
            "mcp.live": {"url": "https://live.example.com/sse", "transport": "sse"},
            "mcp.local": {
                "command": "python",
                "args": ["-m", "index_server"],
                "env": {"INDEX": "x"},
                "roles": ["backend_engineer"],
            },
            "team.backend_engineer": {"tool_groups": ["fs_read", "mcp:docs", "mcp:live"]},
        }
    )

    docs, live, local = mcps_for(settings, member(settings, "backend_engineer")) or []

    assert isinstance(docs, MCPServerHTTP) and docs.url == DOCS_URL
    assert docs.tool_filter is not None
    assert docs.tool_filter({"name": "search_docs"}) is True  # type: ignore[call-arg]
    assert docs.tool_filter({"name": "delete_everything"}) is False  # type: ignore[call-arg]
    assert isinstance(live, MCPServerSSE) and live.tool_filter is None
    assert isinstance(local, MCPServerStdio)
    assert (local.command, local.args, local.env) == (
        "python",
        ["-m", "index_server"],
        {"INDEX": "x"},
    )


def test_no_server_means_no_mcps_at_all() -> None:
    settings = settings_with()

    assert mcps_for(settings, member(settings, "backend_engineer")) is None


def test_docs_mcp_urls_stay_an_alias_for_every_teammate_with_mcp_docs() -> None:
    settings = settings_with(docs_mcp_urls=["https://one.example.com/mcp", "notion"])

    assert mcps_for(settings, member(settings, "backend_engineer")) == [
        "https://one.example.com/mcp",
        "notion",
    ]
    assert mcps_for(settings, member(settings, "codebase_analyst")) is None  # no mcp:docs
    assert mcps_for(settings, member(settings, "engineering_lead")) is None


def test_the_environment_alias_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGINEERING_DOCS_MCP_URLS", "https://a.example.com/mcp, https://b/mcp")
    settings = load_settings()

    assert settings.docs_mcp_urls == ["https://a.example.com/mcp", "https://b/mcp"]


def test_the_smoke_profile_attaches_no_mcp_server_at_all() -> None:
    settings = settings_with(
        profile="smoke", docs_mcp_urls=[DOCS_URL], **{"mcp.docs": {"url": DOCS_URL}}
    )

    assert mcps_for(settings, member(settings, "backend_engineer")) is None
    assert trust_notes(settings, build_roster(settings).all()) == []


def test_an_agent_is_built_with_its_servers_and_nothing_connects() -> None:
    settings = settings_with(**{"mcp.docs": {"url": DOCS_URL, "roles": ["backend_engineer"]}})
    teammate = member(settings, "backend_engineer")

    agent = Agent(
        role=teammate.role,
        goal=teammate.goal,
        backstory=teammate.backstory,
        llm=ScriptedLLM(["done"]),
        **agent_extensions(settings, teammate),
    )

    assert agent.mcps is not None and isinstance(agent.mcps[0], MCPServerHTTP)


# -- the team definition ---------------------------------------------------------------------------


def test_a_tool_group_for_an_undefined_server_is_rejected_with_the_known_ones() -> None:
    settings = settings_with(**{"team.backend_engineer": {"tool_groups": ["fs_read", "mcp:nope"]}})

    with pytest.raises(TeamError, match=r"mcp:nope.*Known:.*mcp:docs"):
        build_roster(settings)


def test_a_defined_server_makes_its_group_valid() -> None:
    settings = settings_with(
        **{
            "mcp.tickets": {"url": "https://tickets.example.com/mcp"},
            "team.backend_engineer": {"tool_groups": ["fs_read", "mcp:tickets"]},
        }
    )

    assert "mcp:tickets" in build_roster(settings).get("backend_engineer").tool_groups


def test_roles_must_name_teammates_that_exist() -> None:
    settings = settings_with(**{"mcp.docs": {"url": DOCS_URL, "roles": ["ghost_engineer"]}})

    with pytest.raises(
        TeamError, match=r"\[mcp.docs\] roles names unknown teammate.*ghost_engineer"
    ):
        build_roster(settings)


# -- the trust warning -----------------------------------------------------------------------------


def test_trust_notes_say_what_each_attached_server_is_and_stay_quiet_about_unused_ones() -> None:
    settings = settings_with(
        **{
            "mcp.docs": {"url": "https://user:pw@docs.example.com/private/path?token=abc"},
            "mcp.local": {"command": "my-server", "roles": ["backend_engineer"]},
            "mcp.unused": {"url": "https://unused.example.com/mcp", "roles": ["debugger"]},
            "team.debugger": {"enabled": False},
        }
    )

    notes = trust_notes(settings, build_roster(settings).all())
    text = "\n".join(notes)

    assert any("'local'" in n and "starts `my-server` on this machine" in n for n in notes)
    assert any("'docs'" in n and "connects to docs.example.com" in n for n in notes)
    assert "unused" not in text  # its only teammate is disabled
    assert "pw" not in text and "token" not in text and "private/path" not in text


def test_team_show_lists_the_mcp_servers_a_teammate_gets() -> None:
    import json
    from pathlib import Path

    from typer.testing import CliRunner

    from engineering_team.cli.app import app

    Path("engineering-team.toml").write_text(
        f'[mcp.docs]\nurl = "{DOCS_URL}"\nroles = ["backend_engineer"]\n', encoding="utf-8"
    )

    shown = CliRunner().invoke(app, ["--json", "team", "show", "backend_engineer"])
    plain = CliRunner().invoke(app, ["team", "show", "backend_engineer"])

    assert json.loads(shown.stdout)["mcp_servers"] == ["docs"]
    assert "MCP servers:" in plain.output and "docs" in plain.output
