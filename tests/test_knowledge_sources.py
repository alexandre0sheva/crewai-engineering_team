"""Optional CrewAI knowledge sources: opt-in, explicit about the embedder, capped, no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from crewai import Agent
from crewai.knowledge.source.string_knowledge_source import StringKnowledgeSource

from engineering_team.extensions import knowledge
from engineering_team.extensions.agents import agent_extensions
from engineering_team.extensions.checks import check_extensions
from engineering_team.extensions.knowledge import (
    KnowledgeError,
    collect_documents,
    embedder_config,
    knowledge_for,
)
from engineering_team.settings import Settings, SettingsError, load_settings
from engineering_team.team import build_roster
from engineering_team.testing import ScriptedLLM

OLLAMA = {"provider": "ollama", "model": "mxbai-embed-large"}


def settings_with(**overrides: Any) -> Settings:
    return load_settings(overrides=overrides)


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    root = tmp_path / "handbook"
    (root / "sub").mkdir(parents=True)
    (root / "intro.md").write_text("# Intro\nWelcome.", encoding="utf-8")
    (root / "sub" / "deploy.txt").write_text("Deploy on Fridays.", encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG")
    (root / ".hidden.md").write_text("secret", encoding="utf-8")
    return root


def test_it_is_off_by_default() -> None:
    settings = settings_with()

    assert settings.knowledge.sources == [] and settings.knowledge.embedder is None
    assert knowledge_for(settings, "backend_engineer") is None
    assert "knowledge_sources" not in agent_extensions(
        settings, build_roster(settings).get("backend_engineer")
    )


def test_sources_without_an_embedder_are_refused_and_the_cost_is_named() -> None:
    with pytest.raises(SettingsError, match=r"knowledge.sources needs knowledge.embedder.*cost"):
        settings_with(**{"knowledge.sources": ["docs"]})


def test_an_unknown_embedding_provider_is_refused() -> None:
    with pytest.raises(SettingsError, match="knowledge.embedder.provider"):
        settings_with(
            **{"knowledge.sources": ["docs"], "knowledge.embedder": {"provider": "mystery"}}
        )


def test_documents_are_collected_in_a_stable_order_text_only(docs: Path, tmp_path: Path) -> None:
    (tmp_path / "STYLE.md").write_text("Be kind.", encoding="utf-8")
    settings = settings_with(
        **{
            "knowledge.sources": [str(docs), str(docs / "intro.md"), "STYLE.md"],
            "knowledge.embedder": OLLAMA,
        }
    )

    found = collect_documents(settings.knowledge, tmp_path)

    # A file named twice (alone, and inside its directory) is embedded once.
    assert [d.label.removeprefix(str(docs)) for d in found] == [
        "/intro.md",
        "/sub/deploy.txt",
        "STYLE.md",  # an explicitly named file keeps the path it was given
    ]
    assert found[1].text == "Deploy on Fridays."
    assert all("secret" not in d.text for d in found)


def test_relative_sources_use_the_given_base(docs: Path, tmp_path: Path) -> None:
    settings = settings_with(
        **{"knowledge.sources": ["handbook/intro.md"], "knowledge.embedder": OLLAMA}
    )

    assert [d.text for d in collect_documents(settings.knowledge, tmp_path)] == [
        "# Intro\nWelcome."
    ]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("missing", "does not exist"),
        ("handbook/image.png", "not a text document"),
    ],
)
def test_a_bad_source_names_the_fix(docs: Path, tmp_path: Path, source: str, message: str) -> None:
    settings = settings_with(**{"knowledge.sources": [source], "knowledge.embedder": OLLAMA})

    with pytest.raises(KnowledgeError, match=message):
        collect_documents(settings.knowledge, tmp_path)


def test_symlinked_sources_are_refused(docs: Path, tmp_path: Path) -> None:
    (tmp_path / "link.md").symlink_to(docs / "intro.md")
    settings = settings_with(**{"knowledge.sources": ["link.md"], "knowledge.embedder": OLLAMA})

    with pytest.raises(KnowledgeError, match="symlink"):
        collect_documents(settings.knowledge, tmp_path)


def test_the_caps_refuse_instead_of_cutting_silently(
    docs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_with(**{"knowledge.sources": [str(docs)], "knowledge.embedder": OLLAMA})
    monkeypatch.setattr(knowledge, "MAX_FILES", 1)

    with pytest.raises(KnowledgeError, match="2 documents.*limits are 1 documents"):
        collect_documents(settings.knowledge, tmp_path)
    monkeypatch.setattr(knowledge, "MAX_FILES", 50)
    monkeypatch.setattr(knowledge, "MAX_DOC_BYTES", 5)
    with pytest.raises(KnowledgeError, match="the limit is 5"):
        collect_documents(settings.knowledge, tmp_path)


def test_check_extensions_reports_a_missing_source(tmp_path: Path) -> None:
    settings = settings_with(**{"knowledge.sources": ["nope.md"], "knowledge.embedder": OLLAMA})

    with pytest.raises(KnowledgeError, match="nope.md"):
        check_extensions(settings, tmp_path)
    with pytest.raises(KnowledgeError, match="nope.md"):
        settings.check_ready(require_credentials=False)


@pytest.mark.parametrize(
    ("embedder", "expected"),
    [
        ({"provider": "openai"}, {"provider": "openai", "config": {}}),
        (
            {"provider": "openai", "model": "text-embedding-3-small"},
            {"provider": "openai", "config": {"model_name": "text-embedding-3-small"}},
        ),
        (
            {"provider": "ollama", "model": "m", "url": "http://localhost:11434/api/embeddings"},
            {
                "provider": "ollama",
                "config": {"model_name": "m", "url": "http://localhost:11434/api/embeddings"},
            },
        ),
        (
            {"provider": "voyageai", "model": "voyage-3"},
            {"provider": "voyageai", "config": {"model": "voyage-3"}},
        ),
    ],
)
def test_the_embedder_becomes_crewais_provider_configuration(
    embedder: dict[str, str], expected: dict[str, Any]
) -> None:
    settings = settings_with(**{"knowledge.sources": ["x.md"], "knowledge.embedder": embedder})

    assert embedder_config(settings.knowledge) == expected


def test_only_the_listed_teammates_get_the_sources(docs: Path) -> None:
    settings = settings_with(
        **{
            "knowledge.sources": [str(docs)],
            "knowledge.embedder": OLLAMA,
            "knowledge.roles": ["Backend Engineer"],
        }
    )

    assert knowledge_for(settings, "frontend_engineer") is None
    sources, embedder = knowledge_for(settings, "backend_engineer") or ([], {})
    assert len(sources) == 2 and all(isinstance(s, StringKnowledgeSource) for s in sources)
    assert embedder["provider"] == "ollama"


def test_an_agent_is_built_with_sources_and_nothing_is_embedded(docs: Path) -> None:
    settings = settings_with(**{"knowledge.sources": [str(docs)], "knowledge.embedder": OLLAMA})
    teammate = build_roster(settings).get("backend_engineer")

    agent = Agent(
        role=teammate.role,
        goal=teammate.goal,
        backstory=teammate.backstory,
        llm=ScriptedLLM(["done"]),
        **agent_extensions(settings, teammate),
    )

    assert agent.knowledge_sources and len(agent.knowledge_sources) == 2
    assert agent.embedder == {"provider": "ollama", "config": {"model_name": "mxbai-embed-large"}}


def test_the_embedders_credential_is_checked_by_name_never_by_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai = {"provider": "openai"}
    settings = settings_with(**{"knowledge.sources": ["x.md"], "knowledge.embedder": openai})
    assert settings.missing_embedder_credentials() == []  # the test environment has a key

    monkeypatch.delenv("OPENAI_API_KEY")
    missing = settings_with(**{"knowledge.sources": ["x.md"], "knowledge.embedder": openai})
    assert (
        "OPENAI_API_KEY is not set (needed to embed knowledge.sources with openai)"
        in (missing.missing_embedder_credentials()[0])
    )
    local = settings_with(**{"knowledge.sources": ["x.md"], "knowledge.embedder": OLLAMA})
    assert local.missing_embedder_credentials() == []
