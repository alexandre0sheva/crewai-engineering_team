"""Hermetic test environment: no developer .env, no real credentials, isolated state."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import dotenv
import pytest

# CrewAI calls ``load_dotenv()`` at import time, which would pull the developer's real
# ``.env`` (API keys, ENGINEERING_* settings) into every test. Neutralise it before
# crewai is imported anywhere; pytest imports this conftest first.
dotenv.load_dotenv = lambda *args, **kwargs: False  # type: ignore[assignment]

TEST_API_KEY = "test-key-not-real"


@pytest.fixture(autouse=True)
def hermetic_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate framework storage, credentials, configuration, and the working directory."""

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    for name in [name for name in os.environ if name.startswith("ENGINEERING_")]:
        monkeypatch.delenv(name)

    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(sandbox / "crewai-storage"))
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", TEST_API_KEY)
    for name in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "AZURE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(sandbox / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(sandbox / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(sandbox / "xdg-data"))
    monkeypatch.chdir(sandbox)
    return sandbox


@pytest.fixture(autouse=True)
def reset_active_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace is still a module global until RunContext replaces it."""

    # ``engineering_team.tools`` re-exports a list called ``workspace_tools`` that shadows
    # the submodule attribute, so import the module by its full path.
    module = importlib.import_module("engineering_team.tools.workspace_tools")
    monkeypatch.setattr(module, "_active_workspace", None)
