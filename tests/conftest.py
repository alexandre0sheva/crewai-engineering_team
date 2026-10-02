"""Hermetic test environment: no developer .env, no real credentials, isolated state."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import dotenv
import pytest

# CrewAI calls ``load_dotenv()`` at import time, which would pull the developer's real
# ``.env`` (API keys, ENGINEERING_* settings) into every test. Neutralise it before
# crewai is imported anywhere; pytest imports this conftest first.
dotenv.load_dotenv = lambda *args, **kwargs: False  # type: ignore[assignment]

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import Settings

TEST_API_KEY = "test-key-not-real"

PROVIDER_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_API_KEY",
)


def docker_is_available() -> bool:
    """True when a Docker daemon answers ``docker info`` quickly."""

    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_BROWSER_PROBE: list[str | None] = []  # the usable Playwright channel (or None), probed once


def usable_browser_channel() -> str | None:
    """The Playwright channel that can launch headless here (``chromium`` or ``chrome``), if any."""

    if _BROWSER_PROBE:
        return _BROWSER_PROBE[0]
    found: str | None = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            for channel in ("chromium", "chrome"):
                try:
                    options = {} if channel == "chromium" else {"channel": channel}
                    playwright.chromium.launch(headless=True, timeout=30_000, **options).close()
                    found = channel
                    break
                except Exception:
                    continue
    except Exception:
        found = None
    _BROWSER_PROBE.append(found)
    return found


def skip_reason(
    keywords: Iterable[str],
    environ: Mapping[str, str],
    docker_available: Callable[[], bool],
    browser_available: Callable[[], bool] | None = None,
) -> str | None:
    """Why a test with these marker names must be skipped by default, or ``None``."""

    names = set(keywords)
    if "live" in names:
        if environ.get("ENGINEERING_LIVE_TESTS") != "1":
            return "live test: set ENGINEERING_LIVE_TESTS=1 to run"
        if not any(environ.get(name) for name in PROVIDER_KEY_NAMES):
            return "live test: no provider API key in the environment"
    if "docker" in names and not docker_available():
        return "docker test: no running Docker daemon"
    if "browser" in names and browser_available is not None and not browser_available():
        return "browser test: Playwright with Chromium or Chrome is not available"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``live`` and ``docker`` tests unless their requirements are met."""

    docker_state: list[bool] = []  # probe the daemon at most once, and only if a test needs it

    def docker_available() -> bool:
        if not docker_state:
            docker_state.append(docker_is_available())
        return docker_state[0]

    for item in items:
        reason = skip_reason(
            (marker.name for marker in item.iter_markers()),
            os.environ,
            docker_available,
            lambda: usable_browser_channel() is not None,
        )
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


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
    for name in PROVIDER_KEY_NAMES[1:]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(sandbox / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(sandbox / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(sandbox / "xdg-data"))
    monkeypatch.chdir(sandbox)
    return sandbox


@pytest.fixture(autouse=True)
def no_external_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to reach a non-loopback address (``live`` tests are exempt)."""

    if request.node.get_closest_marker("live"):
        return
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in (
            "127.0.0.1",
            "::1",
            "localhost",
        ):
            raise RuntimeError(f"Tests must stay offline; blocked a connection to {address!r}.")
        real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def make_context(tmp_path: Path) -> Callable[..., RunContext]:
    """Build a RunContext over a fresh workspace under ``tmp_path``."""

    # Imported here so the dotenv neutralisation above happens before crewai loads.
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import load_settings
    from engineering_team.tools.workspace import ProjectWorkspace

    def build(
        name: str = "project",
        *,
        settings: Settings | None = None,
        extra_commands: Iterable[str] = (),
        env_passthrough: Iterable[str] = (),
    ) -> RunContext:
        workspace = ProjectWorkspace.create(
            tmp_path / name, extra_commands=extra_commands, env_passthrough=env_passthrough
        )
        return RunContext.create(settings or load_settings(), workspace)

    return build


class Toolbox:
    """A RunContext plus its built tools, callable by tool name."""

    def __init__(self, ctx: RunContext, tools: list) -> None:  # type: ignore[type-arg]
        self.ctx = ctx
        self.workspace = ctx.workspace
        self.tools = {tool.name: tool for tool in tools}

    def __call__(self, name: str, /, **arguments: object) -> str:
        return self.tools[name].run(**arguments)

    def write(self, path: str, content: str) -> None:
        self.workspace.write_file(path, content)

    def read(self, path: str) -> str:
        return (self.workspace.root / path).read_text(encoding="utf-8")


@pytest.fixture
def make_toolbox(make_context: Callable[..., RunContext]) -> Callable[..., Toolbox]:
    """Build tools for a fresh workspace; options go to ``build_tools``."""

    from engineering_team.tools import build_tools

    def build(name: str = "project", **options: object) -> Toolbox:
        ctx = make_context(name)
        return Toolbox(ctx, build_tools(ctx, **options))  # type: ignore[arg-type]

    return build


@pytest.fixture
def toolbox(make_toolbox: Callable[..., Toolbox]) -> Toolbox:
    return make_toolbox()
