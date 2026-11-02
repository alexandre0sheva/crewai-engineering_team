"""Diagnostics, Go To Definition and precise Find References over a fake language server."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Toolbox

from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import Settings, load_settings
from engineering_team.tools import build_tools
from engineering_team.tools.registry import LSP_TOOLS

SERVER = Path(__file__).with_name("fake_lsp_server.py")
MODULE = "def load(path):\n    return path  # BAD\n\n\nvalue = load('x')  # TODO\n"
USER = "from app import load\n\nresult = load('y')\n"
MakeContext = Callable[..., RunContext]


def settings_with(**lsp: object) -> Settings:
    options = {
        "enabled": True,
        "servers": {"python": [sys.executable, str(SERVER)]},
        "request_timeout_seconds": 5,
        "diagnostics_wait_seconds": 5,
        **lsp,
    }
    return load_settings(overrides={"lsp": options})


def toolbox_of(ctx: RunContext) -> Toolbox:
    return Toolbox(ctx, build_tools(ctx, lazy=False))


@pytest.fixture
def box(make_context: MakeContext) -> Toolbox:
    toolbox = toolbox_of(make_context(settings=settings_with()))
    toolbox.write("app.py", MODULE)
    toolbox.write("user.py", USER)
    return toolbox


def event_types(toolbox: Toolbox) -> list[str]:
    return [e.type for e in read_events(toolbox.ctx.run_dir / "events.jsonl")]


def test_the_server_tools_exist_only_when_the_bridge_is_enabled(make_context: MakeContext) -> None:
    off = toolbox_of(make_context())
    on = toolbox_of(make_context("other", settings=settings_with()))

    assert {"Diagnostics", "Go To Definition"} == LSP_TOOLS
    assert not LSP_TOOLS & set(off.tools)
    assert set(on.tools) >= LSP_TOOLS


def test_diagnostics_of_a_file_list_its_problems_errors_first(box: Toolbox) -> None:
    text = box("Diagnostics", path="app.py")

    assert "1 error(s), 1 warning(s)" in text
    lines = text.splitlines()
    assert lines[1] == "app.py:2:20 error [bad-found] BAD found on this line"
    assert lines[2].startswith("app.py:5:22 warning [todo-found]")


def test_diagnostics_of_the_project_cover_every_supported_file(box: Toolbox) -> None:
    box.write("notes.md", "BAD text no server reads\n")

    text = box("Diagnostics", path=".")

    assert "for 2 file(s)" in text and "app.py:2:20" in text
    assert "notes.md" not in text and "user.py" not in text.split("\n", 1)[1]  # no problems there


def test_diagnostics_are_bounded(box: Toolbox) -> None:
    box.write("many.py", "BAD\n" * 40)

    text = box("Diagnostics", path="many.py", max_results=5)

    assert "40 error(s)" in text and "35 more" in text


def test_diagnostics_of_a_language_without_a_server_say_so(box: Toolbox) -> None:
    box.write("notes.md", "text\n")

    assert box("Diagnostics", path="notes.md").startswith("ERROR: No language server is configured")
    box.write("docs/readme.md", "text\n")
    assert "No file with a supported language server" in box("Diagnostics", path="docs")


def test_go_to_definition_resolves_a_use_to_its_definition(box: Toolbox) -> None:
    text = box("Go To Definition", path="app.py", line=5, symbol="load")

    assert text.splitlines() == [
        "Definition(s) from " + sys.executable + ":",
        "app.py:1:5  def load(path):",
    ]


def test_go_to_definition_accepts_a_column_and_explains_a_wrong_position(box: Toolbox) -> None:
    assert "app.py:1:5" in box("Go To Definition", path="app.py", line=5, column=9)
    assert "does not occur on that line" in box(
        "Go To Definition", path="app.py", line=1, symbol="zzz"
    )
    assert "has 5 lines" in box("Go To Definition", path="app.py", line=99, symbol="load")
    assert "symbol" in box("Go To Definition", path="app.py", line=5)  # neither symbol nor column


def test_a_position_without_a_definition_points_to_find_symbol(box: Toolbox) -> None:
    assert "found no definition" in box("Go To Definition", path="app.py", line=2, symbol="return")


def test_find_references_is_exact_when_a_server_answers(box: Toolbox) -> None:
    box(
        "Diagnostics", path="user.py"
    )  # a real server indexes the project; the fake knows opened files

    text = box("Find References", symbol="load")

    head, *rest = text.splitlines()
    assert head.startswith("4 reference(s) to load in 2 file(s)") and "language server" in head
    assert rest == [
        "app.py:1:5  def load(path):",
        "app.py:5:9  value = load('x')  # TODO",
        "user.py:1:17  from app import load",
        "user.py:3:10  result = load('y')",
    ]


def test_find_references_with_a_position_uses_it_when_the_name_is_ambiguous(box: Toolbox) -> None:
    box.write("other.py", "def load():\n    pass\n")

    ambiguous = box("Find References", symbol="load")
    pinned = box("Find References", symbol="load", file="app.py", line=1)

    assert ambiguous.startswith("No language-server answer (")
    assert "has 2 definitions" in ambiguous and "word-match results follow" in ambiguous
    assert "app.py" in ambiguous and "call" in ambiguous  # the old tool's answer is still there
    assert "(language server: exact, scope-aware)" in pinned


def test_find_references_falls_back_and_says_so_when_no_server_is_installed(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engineering_team.execution.backend.shutil.which", lambda name: None)
    settings = load_settings(overrides={"lsp": {"enabled": True}})
    box = toolbox_of(make_context(settings=settings))
    box.write("app.py", MODULE)

    text = box("Find References", symbol="load")

    assert text.startswith("No language-server answer (No python language server is installed")
    assert "pyright-langserver" in text and "lsp.servers.python" in text
    assert "app.py" in text and "call" in text  # the heuristic's classified results
    assert "ERROR: No python language server" in box("Diagnostics", path="app.py")
    assert event_types(box).count("lsp.unavailable") == 1  # the answer is remembered


def test_find_references_is_unchanged_when_the_bridge_is_off(make_context: MakeContext) -> None:
    box = toolbox_of(make_context())
    box.write("app.py", MODULE)

    text = box("Find References", symbol="load")

    assert "language server" not in text.lower() and "app.py" in text


def test_one_server_serves_a_language_for_the_whole_run(box: Toolbox) -> None:
    box("Diagnostics", path="app.py")
    box("Go To Definition", path="user.py", line=3, symbol="load")
    box("Find References", symbol="load")

    assert event_types(box).count("lsp.started") == 1


def test_servers_are_stopped_when_the_run_ends(box: Toolbox) -> None:
    box("Diagnostics", path="app.py")
    manager = box.ctx.lsp
    assert manager is not None
    (client,) = manager._clients.values()

    manager.close_all("the run ended")

    assert client._process.poll() is not None
    assert "lsp.stopped" in event_types(box)
    assert manager._clients == {}


def test_cancelling_the_run_kills_a_started_server(box: Toolbox) -> None:
    box("Diagnostics", path="app.py")
    manager = box.ctx.lsp
    assert manager is not None
    (client,) = manager._clients.values()

    box.ctx.cancel_event.set()

    deadline = time.monotonic() + 5
    while client._process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client._process.poll() is not None
    manager.close_all("cancelled")


def test_a_server_that_cannot_start_is_reported_once(make_context: MakeContext) -> None:
    crash = [sys.executable, "-c", "import sys; sys.exit(3)"]
    box = toolbox_of(make_context(settings=settings_with(servers={"python": crash})))
    box.write("app.py", MODULE)

    first = box("Diagnostics", path="app.py")
    second = box("Diagnostics", path="app.py")

    assert first.startswith("ERROR:") and "did not start" in first and first == second
    assert event_types(box).count("lsp.unavailable") == 1


def test_a_server_that_dies_mid_run_is_not_restarted(make_context: MakeContext) -> None:
    box = toolbox_of(
        make_context(
            settings=settings_with(servers={"python": [sys.executable, str(SERVER), "--die"]})
        )
    )
    box.write("app.py", MODULE)

    first = box("Diagnostics", path="app.py")
    time.sleep(0.3)
    second = box("Diagnostics", path="app.py")

    assert first.startswith("ERROR:") and "stopped earlier in this run" in second
    assert event_types(box).count("lsp.started") == 1


def test_a_server_starts_with_the_environment_project_commands_get(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Found by running real pyright: a server found through PATH needs PATH (and an isolated HOME).
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    ctx = make_context(settings=settings_with(servers={"python": ["lsp-under-test", "--stdio"]}))
    seen = []

    class Recorder:
        def open_stdio(self, spec: object) -> object:
            seen.append(spec)
            raise OSError("not started in this test")

    monkeypatch.setattr("engineering_team.codeintel.lsp_servers.has_executable", lambda *a: True)
    ctx.lsp._backend = Recorder()  # type: ignore[union-attr]

    with pytest.raises(Exception, match="Could not start"):
        ctx.lsp.client_for("app.py")  # type: ignore[union-attr]

    (spec,) = seen
    assert spec.env["PATH"] == "/usr/bin:/bin"  # type: ignore[attr-defined]
    assert spec.env["HOME"].startswith(str(ctx.workspace.root))  # type: ignore[attr-defined]
    assert "OPENAI_API_KEY" not in spec.env  # type: ignore[attr-defined]
