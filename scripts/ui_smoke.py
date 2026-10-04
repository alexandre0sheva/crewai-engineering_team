"""Browser smoke test of the web UI in demo mode: new run -> run page -> results.

    uv run python scripts/ui_smoke.py [--headed] [--screenshots DIR]

Starts ``engineering-team ui --demo`` on a free port, drives the page with Playwright (the
optional ``browser`` extra, plus Chromium or Chrome), and checks the path a person takes without a
terminal: describe a change, pick the sample project, start, watch the run finish, open the diff.
Exits 0 when it passes or when there is no browser to drive (it says so), 1 when a step fails.
Nothing here calls a model or needs a key.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REQUEST = "Add a notes search command.\n\n## Acceptance criteria\n- AC-1 search finds notes."


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"The UI server exited early (code {process.returncode}).")
        try:
            urllib.request.urlopen(f"{base}/api/v1/health", timeout=2).read()  # noqa: S310
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("The UI server did not start within 60 seconds.")


def drive(base: str, headed: bool, shots: Path | None) -> list[str]:
    from playwright.sync_api import expect, sync_playwright

    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = None
        for options in ({}, {"channel": "chrome"}):
            try:
                browser = playwright.chromium.launch(headless=not headed, **options)
                break
            except Exception:  # noqa: BLE001 - try the next way of getting a browser
                continue
        if browser is None:
            print("skipped: no Chromium or Chrome that Playwright can launch")
            return problems
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        console: list[str] = []
        page.on("console", lambda m: m.type == "error" and console.append(m.text))
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on(  # the console message for a failed load does not say which URL it was
            "response",
            lambda r: (
                r.status >= 400 and console.append(f"HTTP {r.status} {r.request.method} {r.url}")
            ),
        )
        page.goto(f"{base}/")
        expect(page.get_by_role("heading", name="New run")).to_be_visible()
        expect(page.get_by_text("Demo mode.")).to_be_visible()

        page.get_by_role("tab", name="Add feature").click()
        page.get_by_role("button", name="Use the demo sample project").click()
        expect(page.get_by_text("Clean tree on")).to_be_visible(timeout=15_000)
        page.get_by_label("Describe the feature").fill(REQUEST)
        page.get_by_role("button", name="Start run").click()

        expect(page).to_have_url(__import__("re").compile(r"#/runs/\d{8}-\d{6}-[0-9a-f]{6}$"))
        expect(page.get_by_role("heading", level=1)).to_contain_text("Add feature", timeout=30_000)
        expect(page.locator("#live")).not_to_be_empty()
        # The demo team asks a question and blocks a card until it is answered.
        answer = page.locator("dialog[open] #answer-text")
        expect(answer).to_be_visible(timeout=120_000)
        answer.fill("Markdown")
        page.get_by_role("button", name="Send answer").click()
        expect(page.locator(".kcard").first).to_be_visible()
        expect(page.get_by_text("Succeeded").first).to_be_visible(timeout=180_000)
        expect(page.locator(".b-cell[data-status=done] .kcard").first).to_be_visible()
        if shots:
            page.screenshot(path=str(shots / "run.png"))

        # A card opens in the drawer with its history and tool calls; the feed and replay work.
        page.locator(".kcard[data-id='K-001']").click()
        expect(page.locator("dialog.drawer[open] h2")).to_be_visible()
        expect(page.get_by_text("Status history")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role("tab", name="Timeline").click()
        expect(page.locator(".g-bar").first).to_be_visible()
        page.get_by_role("tab", name="Activity").click()
        expect(page.locator(".f-row").first).to_be_visible()
        page.get_by_role("tab", name="Board").click()
        page.get_by_role("button", name="Replay this run").click()
        expect(page.locator("#replay-slider")).to_be_visible()
        page.locator("#replay-slider").fill("40")
        expect(page.locator(".replay-time")).to_contain_text(":")
        page.get_by_role("button", name="Back to the end").click()

        page.get_by_role("link", name="Results").click()
        expect(page.get_by_role("heading", level=1)).to_contain_text("Verified")
        page.get_by_role("tab", name="Criteria").click()
        expect(page.get_by_text("AC-1").first).to_be_visible()
        page.get_by_role("tab", name="Changes").click()
        expect(page.locator(".diff-file").first).to_be_visible()
        expect(page.locator(".diff-lines .add").first).to_be_visible()
        page.get_by_role("tab", name="Take the change").click()
        expect(page.get_by_text("git merge").first).to_be_visible()
        if shots:
            page.screenshot(path=str(shots / "results.png"))

        page.get_by_role("navigation", name="Main").get_by_role("link", name="Runs").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Runs")
        expect(page.locator("tbody tr")).to_have_count(1)

        ids = page.evaluate(
            "[...document.querySelectorAll('[id]')].map(e => e.id)"
            ".filter((id, i, all) => all.indexOf(id) !== i)"
        )
        if ids:
            problems.append(f"duplicate element ids: {ids}")
        problems += [f"console error: {text}" for text in console]
        browser.close()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--screenshots", type=Path, help="save a screenshot of each screen here")
    args = parser.parse_args()
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed (uv sync --extra browser)")
        return 0
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(  # noqa: S603 - an argument list, never a shell
        [sys.executable, "-m", "engineering_team", "ui", "--demo", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(base, server)
        problems = drive(base, args.headed, args.screenshots)
    except Exception as exc:  # noqa: BLE001 - a failed expectation is a failed smoke test
        print(f"FAILED: {exc}")
        return 1
    finally:
        server.terminate()
        server.wait(10)
    for problem in problems:
        print(f"FAILED: {problem}")
    if not problems:
        print("ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
