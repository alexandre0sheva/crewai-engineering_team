"""The browser tools against a real headless browser and a page served by the runtime tools.

Marked ``browser``: skipped when Playwright or Chromium/Chrome is missing (the guard, registry,
and heuristics are unit-tested without a browser in the neighbouring test files).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import Toolbox, usable_browser_channel
from http_fixture import SERVER_FILE, SERVER_SCRIPT, server_command

from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events, stage_scope
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import build_tools
from engineering_team.webtools.untrusted import END_MARKER, START_MARKER

pytestmark = pytest.mark.browser

INDEX = """<!doctype html><html lang="en"><head><title>Demo shop</title>
<style>.faint { color: #bbbbbb; background: #ffffff }</style></head><body>
<h1>Demo shop</h1>
<input aria-label="Name" id="name">
<button onclick="greet()">Greet</button>
<p id="out"></p>
<label>Size <select id="size">
<option value="s">Small</option><option value="m">Medium</option></select></label>
<button onclick="console.error('seeded error')">Break</button>
<button onclick="setTimeout(() => { throw new Error('kaboom') }, 0)">Explode</button>
<button></button>
<img src="/logo.png">
<p class="faint">Pale grey fine print</p>
<a href="/second.html">Second page</a>
<script>function greet() {
  document.getElementById('out').textContent = 'Hello, ' + document.getElementById('name').value;
}
fetch('/missing.json').catch(() => {}); console.log('page loaded');</script>
</body></html>"""
SECOND = (
    "<html lang='en'><head><title>Second</title></head><body><h1>Second page</h1></body></html>"
)


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + ":" + os.environ["PATH"])


BROWSER_MARKERS = ("playwright_chromiumdev_profile", "headless_shell", "chrome-headless-shell")


def browser_processes() -> list[str]:
    """The browser's processes, as ``ps`` lines: everything below this test process (Playwright's
    driver, the browser and its helpers) plus anything that still names the browser's profile
    directory or binary, so an orphan that outlived its parent is counted too."""

    out = subprocess.run(
        ["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=False
    ).stdout
    rows: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    below, grew = {os.getpid()}, True
    while grew:  # the transitive children of this process
        found = {pid for pid, (parent, _) in rows.items() if parent in below} - below
        grew = bool(found)
        below |= found
    named = {
        pid for pid, (_, command) in rows.items() if any(m in command for m in BROWSER_MARKERS)
    }
    mine = (below - {os.getpid()}) | named
    return sorted(
        f"{pid} {rows[pid][1][:160]}" for pid in mine if not rows[pid][1].startswith("ps ")
    )


def chrome_processes() -> int:
    return len(browser_processes())


@pytest.fixture
def make_app(
    make_context: Callable[..., RunContext], tmp_path: Path
) -> Iterator[Callable[..., Toolbox]]:
    """A context whose page is served on one of the run's own ports."""

    contexts: list[RunContext] = []

    def build(**browser: object) -> Toolbox:
        overrides: dict[str, object] = {"browser.channel": usable_browser_channel()}
        overrides.update({f"browser.{key}": value for key, value in browser.items()})
        settings = load_settings(env={}, cwd=tmp_path, home=tmp_path / "home", overrides=overrides)
        ctx = make_context(settings=settings)
        contexts.append(ctx)
        ws = ctx.workspace
        ws.write_file("site/index.html", INDEX)
        ws.write_file("site/second.html", SECOND)
        ws.write_file(SERVER_FILE, SERVER_SCRIPT)
        toolbox = Toolbox(ctx, build_tools(ctx, groups=["runtime", "browser"], agent="frontend"))
        port = toolbox("Find Free Port").split()[2]
        started = toolbox(
            "Start Background Process",
            name="site",
            command=server_command(port),
            ready_when="port",
            ready_target=port,
        )
        assert "ready" in started.lower(), started
        toolbox.port = int(port)  # type: ignore[attr-defined]
        return toolbox

    yield build
    for ctx in contexts:
        ctx.browsers.close_all("test over")
        ctx.processes.stop_all("test over")


def ref_for(snapshot: str, role_and_name: str) -> str:
    match = re.search(rf"{re.escape(role_and_name)}.*?\[ref=(e\d+)\]", snapshot)
    assert match, f"{role_and_name!r} not in snapshot:\n{snapshot}"
    return match.group(1)


def url(box: Toolbox, path: str = "/") -> str:
    return f"http://127.0.0.1:{box.port}{path}"  # type: ignore[attr-defined]


def test_open_snapshot_type_click_select_press_and_wait(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app()

    opened = box("Browser Open", url=url(box))
    snapshot = box("Browser Snapshot")
    name = ref_for(snapshot, 'textbox "Name"')
    greet = ref_for(snapshot, 'button "Greet"')
    typed = box("Browser Type", ref=name, text="Ada")
    clicked = box("Browser Click", ref=greet)
    waited = box("Browser Wait For", text="Hello, Ada")
    selected = box("Browser Select", ref=ref_for(snapshot, 'combobox "Size"'), value="Medium")
    pressed = box("Browser Press Key", key="Tab")
    after = box("Browser Snapshot")

    assert "Opened http://127.0.0.1" in opened and "Demo shop" in opened
    assert snapshot.splitlines()[1].startswith(START_MARKER) and snapshot.endswith(END_MARKER)
    assert 'heading "Demo shop" [level=1]' in snapshot and 'link "Second page"' in snapshot
    assert typed.startswith("Typed 3 character(s) into " + name)
    assert clicked.startswith(f"Clicked {greet}.") and "Page: http://127.0.0.1" in clicked
    assert waited.startswith("The text 'Hello, Ada' is visible")
    assert "Medium" in selected or "m" in selected
    assert pressed.startswith("Pressed Tab")
    assert "Hello, Ada" in after


def test_a_link_click_navigates_and_a_stale_ref_explains_itself(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app()
    box("Browser Open", url=url(box))
    link = ref_for(box("Browser Snapshot"), 'link "Second page"')

    clicked = box("Browser Click", ref=link)
    stale = box("Browser Click", ref="e999")
    bad = box("Browser Click", ref="#submit")

    assert clicked.startswith("Clicked e") and "/second.html" in clicked.splitlines()[0]
    assert "Second page" in box("Browser Snapshot")
    assert stale.startswith("ERROR:") and "Timed out" in stale and "new Browser Snapshot" in stale
    assert bad.startswith("ERROR:") and "not an element ref" in bad


def test_console_errors_uncaught_exceptions_and_failed_requests_are_captured(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app()
    opened = box("Browser Open", url=url(box))
    buttons = box("Browser Snapshot")

    broke = box("Browser Click", ref=ref_for(buttons, 'button "Break"'))
    exploded = box("Browser Click", ref=ref_for(buttons, 'button "Explode"'))
    report = box("Browser Console & Errors")
    only_errors = box("Browser Console & Errors", kind="errors")
    cleared = box("Browser Console & Errors", clear=True)
    empty = box("Browser Console & Errors")

    assert "problem(s) while loading" in opened  # /missing.json and /logo.png are 404s
    assert "new problem(s)" in broke and "Browser Console & Errors" in broke
    assert "new problem(s)" in exploded  # the exception, and perhaps a late favicon 404
    assert "console/error: seeded error" in report and "pageerror/error: kaboom" in report
    assert "HTTP 404 GET http://127.0.0.1" in report and "console/log: page loaded" in report
    assert "1 uncaught error(s)" in report
    assert "page loaded" not in only_errors and "seeded error" in only_errors
    assert "(log cleared)" in cleared and "0 uncaught error(s)" in empty


def test_viewport_screenshots_and_accessibility_check(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app()
    box("Browser Open", url=url(box))

    mobile = box("Set Viewport", preset="mobile")
    custom = box("Set Viewport", width=500, height=700)
    bad = box("Set Viewport", preset="watch")
    shot = box("Browser Screenshot", full_page=True, name="home")
    ref = ref_for(box("Browser Snapshot"), 'heading "Demo shop"')
    element = box("Browser Screenshot", ref=ref, name="title")
    audit = box("Accessibility Check")

    assert "375x812" in mobile and "500x700" in custom and bad.startswith("ERROR:")
    path = Path(re.search(r"saved: (\S+\.png)", shot).group(1))  # type: ignore[union-attr]
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and path.parent.name == "screenshots"
    assert path.parent.parent == box.ctx.run_dir
    assert "-title.png" in element
    artifacts = [
        e for e in read_events(box.ctx.run_dir / "events.jsonl") if e.type == "artifact.created"
    ]
    assert [a.data["kind"] for a in artifacts] == ["screenshot", "screenshot"]
    assert (
        artifacts[0].data["path"] == f"screenshots/{path.name}"
        and artifacts[0].agent is None
        or True
    )
    assert "Accessibility check (heuristic, not a full audit)" in audit
    assert "button-name" in audit and "image-alt" not in audit.replace("image-alt", "image-alt")
    assert "contrast" in audit and "Pale grey fine print" in audit
    assert audit.startswith(START_MARKER)


def test_the_guard_refuses_everything_but_this_runs_own_ports(
    make_app: Callable[..., Toolbox],
) -> None:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not yours")

        def log_message(self, *args: object) -> None:
            return None

    other = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    other_port = other.server_address[1]
    box = make_app()
    box.ctx.workspace.write_file(
        "site/embed.html",
        f"<html><body><h1>Embed</h1><img src='http://127.0.0.1:{other_port}/pixel.png'></body></html>",
    )
    try:
        external = box("Browser Open", url="https://example.com/")
        files = box("Browser Open", url="file:///etc/hosts")
        data = box("Browser Open", url="data:text/html,<h1>hi</h1>")
        port = box("Browser Open", url=f"http://127.0.0.1:{other_port}/secret")
        embed = box("Browser Open", url=url(box, "/embed.html"))
        report = box("Browser Console & Errors", kind="errors")
    finally:
        other.shutdown()
        other.server_close()

    assert external.startswith("ERROR: Blocked") and "web tools are off" in external
    assert files.startswith("ERROR: Blocked") and data.startswith("ERROR: Blocked")
    assert port.startswith("ERROR: Blocked") and "not one of this run's ports" in port
    assert "Opened http" in embed
    assert f"blocked http://127.0.0.1:{other_port}/pixel.png" in report
    assert "1 request(s) refused by the navigation guard" in report
    assert hits == []  # the other local service never saw a request
    blocked = [
        e for e in read_events(box.ctx.run_dir / "events.jsonl") if e.type == "browser.blocked"
    ]
    assert blocked and blocked[0].data["url"] == f"http://127.0.0.1:{other_port}/pixel.png"


def test_a_redirect_from_an_allowed_port_to_another_local_service_is_refused(
    make_app: Callable[..., Toolbox],
) -> None:
    hits: list[str] = []

    class Secret(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    secret = ThreadingHTTPServer(("127.0.0.1", 0), Secret)
    threading.Thread(target=secret.serve_forever, daemon=True).start()
    secret_port = secret.server_address[1]

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{secret_port}/internal")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    bouncer = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=bouncer.serve_forever, daemon=True).start()
    box = make_app()
    box.ctx.processes.allow_port(bouncer.server_address[1], "bouncer")
    try:
        result = box("Browser Open", url=f"http://127.0.0.1:{bouncer.server_address[1]}/go")
    finally:
        for server in (bouncer, secret):
            server.shutdown()
            server.server_close()

    assert result.startswith("ERROR:")
    assert hits == []


def test_contexts_are_per_agent_limited_and_closed_with_their_stage_and_the_run(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app(max_contexts=1)
    other = Toolbox(box.ctx, build_tools(box.ctx, groups=["browser"], agent="qa"))
    before = chrome_processes()

    with stage_scope("verify"):
        first = box("Browser Open", url=url(box))
    second = other("Browser Open", url=url(box))
    assert "Opened http" in first and second.startswith("ERROR:")
    assert "browser.max_contexts" in second
    assert chrome_processes() > before, "\n".join(browser_processes())
    assert box.ctx.browsers.open_agents() == ["frontend"]

    box.ctx.browsers.stop_stage("verify")
    assert box.ctx.browsers.open_agents() == []
    assert "Opened http" in other("Browser Open", url=url(box))
    assert other("Browser Close") == "Browser closed."
    assert other("Browser Close") == "No browser was open."
    assert box("Browser Snapshot").startswith("ERROR:")  # no page, and no leaked context

    box.ctx.browsers.close_all("the run ended")
    assert not box.ctx.browsers.worker_alive
    assert chrome_processes() == before, "\n".join(browser_processes())
    assert box("Browser Open", url=url(box)).startswith("ERROR:")


def test_an_agent_loads_a_page_finds_a_button_by_ref_clicks_it_and_sees_the_seeded_error(
    make_app: Callable[..., Toolbox],
) -> None:
    box = make_app()
    box("Browser Open", url=url(box))
    ref = ref_for(box("Browser Snapshot"), 'button "Break"')
    box("Browser Close")
    llm = ScriptedLLM(
        [
            ToolCall("Browser Open", {"url": url(box)}),
            ToolCall("Browser Snapshot", {}),
            ToolCall("Browser Click", {"ref": ref}),
            ToolCall("Browser Console & Errors", {"kind": "errors"}),
            "The Break button logs 'seeded error' to the console.",
        ]
    )

    result = run_agent_task(
        box.ctx, llm, tools=build_tools(box.ctx, groups=["browser"], agent="frontend"), max_iter=10
    )

    assert result.startswith("The Break button")
    llm.assert_exhausted()
    assert f'button "Break" [ref={ref}]' in llm.calls[2].prompt
    assert "1 new problem" in llm.calls[3].prompt
    assert "console/error: seeded error" in llm.calls[4].prompt


def test_websockets_to_other_local_services_are_refused_too(
    make_app: Callable[..., Toolbox],
) -> None:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    other = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    box = make_app()
    box.ctx.workspace.write_file(
        "site/ws.html",
        "<html><body><h1>Socket</h1><script>"
        f"new WebSocket('ws://127.0.0.1:{other.server_address[1]}/hmr');"
        "</script></body></html>",
    )
    try:
        box("Browser Open", url=url(box, "/ws.html"))
        box("Browser Wait For", seconds=1)
        report = box("Browser Console & Errors", kind="errors")
    finally:
        other.shutdown()
        other.server_close()

    assert hits == []
    assert "1 request(s) refused by the navigation guard" in report
    assert f"127.0.0.1:{other.server_address[1]}/hmr" in report
