"""What each browser tool does to a page. Every function runs on the browser worker thread.

Results are short and structured; whatever the page controls (titles, text, console output) is
returned inside an untrusted-content block. Playwright errors are turned into one-line messages
that say what to do next.
"""

from __future__ import annotations

import re
from typing import Any

from engineering_team.browsertools import scripts
from engineering_team.browsertools.a11y import (
    ContrastIssue,
    DomFacts,
    check,
    format_report,
    parse_snapshot,
)
from engineering_team.browsertools.driver import PlaywrightSession
from engineering_team.tools.support import ToolError
from engineering_team.webtools.untrusted import wrap_untrusted

REF = re.compile(r"(?:f\d+)?e\d+")
VIEWPORTS = {
    "desktop": (1280, 800),
    "tablet": (768, 1024),
    "mobile": (375, 812),
}
WAIT_STATES = ("load", "domcontentloaded", "networkidle", "commit")
MAX_TYPED_CHARS = 5000
MAX_WAIT_SECONDS = 10
MAX_SCREENSHOTS = 100
SETTLE_MS = 150


def translate(exc: Exception) -> ToolError:
    """Playwright's multi-line errors as one actionable line (anything else passes through)."""

    if isinstance(exc, ToolError):
        return exc
    module = type(exc).__module__
    if not module.startswith("playwright"):
        raise exc
    first = next((ln.strip() for ln in str(exc).splitlines() if ln.strip()), type(exc).__name__)
    first = first.removeprefix("Locator.").removeprefix("Page.")
    if type(exc).__name__ == "TimeoutError":
        return ToolError(
            f"Timed out: {first}. The element may not exist (take a new Browser Snapshot; refs "
            "change when the page changes), be hidden, or be covered by another element."
        )
    if "Target page, context or browser has been closed" in str(exc):
        return ToolError("The page was closed. Use Browser Open to load it again.")
    return ToolError(first)


def page_of(session: PlaywrightSession) -> Any:
    page = session.page
    if page is None or page.is_closed():
        raise ToolError("No page is open. Use Browser Open with the app's URL first.")
    return page


def locator(session: PlaywrightSession, ref: str) -> Any:
    text = ref.strip().removeprefix("[").removesuffix("]").removeprefix("ref=")
    if not REF.fullmatch(text):
        raise ToolError(
            f"'{ref}' is not an element ref. Refs look like e12 and come from Browser Snapshot."
        )
    return page_of(session).locator(f"aria-ref={text}")


def _after(session: PlaywrightSession, note: str) -> str:
    page = page_of(session)
    page.wait_for_timeout(SETTLE_MS)  # let handlers run and their console output arrive
    text = f"{note}. Page: {page.url}"
    fresh = session.new_problems()
    if fresh:
        text += (
            f"\n! {fresh} new problem(s) on the page (console errors, uncaught exceptions, failed "
            "requests): see Browser Console & Errors."
        )
    return text


# -- opening and reading -------------------------------------------------------------------------


def open_url(session: PlaywrightSession, url: str, wait_until: str) -> str:
    if wait_until not in WAIT_STATES:
        raise ToolError(f"wait_until must be one of: {', '.join(WAIT_STATES)}.")
    if reason := session.guard.check(url):
        raise ToolError(f"Blocked: {reason}")
    page = session.ensure_page()
    session.armed = True
    refused_before = len(session.refusals)
    try:
        response = page.goto(url, wait_until=wait_until)
    except Exception as exc:
        if len(session.refusals) > refused_before:  # a redirect hop or tunnel the guard refused
            raise ToolError(f"Blocked: {session.refusals[-1].reason}") from exc
        raise translate(exc) from exc
    if response is not None and response.headers.get("x-browser-guard"):
        raise ToolError(f"Blocked: {session.refusals[-1].reason}")
    status = f"HTTP {response.status}" if response is not None else "no response"
    title = (page.title() or "").strip()[:100]
    lines = [f"Opened {page.url} ({status}); title: {title or '(none)'}"]
    if response is not None and response.status >= 400:
        lines.append(f"! The server answered {response.status}; check the app's logs.")
    fresh = session.new_problems()
    if fresh:
        lines.append(f"! {fresh} problem(s) while loading: see Browser Console & Errors.")
    lines.append("Next: Browser Snapshot to read the page.")
    return wrap_untrusted(page.url, "\n".join(lines)) if title else "\n".join(lines)


def snapshot(session: PlaywrightSession, selector: str, max_chars: int) -> str:
    page = page_of(session)
    try:
        target = page.locator(selector).first if selector.strip() else page.locator("body")
        text = target.aria_snapshot(mode="ai")
    except Exception as exc:
        raise translate(exc) from exc
    limit = max_chars or session.settings.max_snapshot_chars
    if len(text) > limit:
        text = (
            f"{text[:limit]}\n... snapshot cut at {limit} characters; pass selector= (a CSS "
            "selector) to read one part of the page."
        )
    size = page.viewport_size or {"width": 0, "height": 0}
    head = (
        f"Page {page.url} (viewport {size['width']}x{size['height']}); "
        "refs like e3 work in Click/Type/Select"
    )
    body = text or "(the page has no accessible content yet; try Browser Wait For)"
    return f"{head}\n{wrap_untrusted(page.url, body)}"


def screenshot(session: PlaywrightSession, ref: str, full_page: bool, name: str) -> str:
    page = page_of(session)
    directory = session.screenshots_dir
    directory.mkdir(parents=True, exist_ok=True)
    if len(list(directory.glob("*.png"))) >= MAX_SCREENSHOTS:
        raise ToolError(f"This run already has {MAX_SCREENSHOTS} screenshots; no more are saved.")
    session.shots += 1
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip() or "shot").strip("-")[:40] or "shot"
    agent = re.sub(r"[^A-Za-z0-9_-]+", "-", session.agent)[:30] or "agent"
    path = directory / f"{agent}-{session.shots:03d}-{label}.png"
    try:
        if ref.strip():
            locator(session, ref).screenshot(path=str(path))
        else:
            page.screenshot(path=str(path), full_page=full_page)
    except Exception as exc:
        raise translate(exc) from exc
    size = path.stat().st_size
    relative = f"screenshots/{path.name}"
    session.events.emit(
        "artifact.created",
        kind="screenshot",
        path=relative,
        url=page.url.split("?", 1)[0],
        agent=session.agent,
        bytes=size,
    )
    return (
        f"Screenshot saved: {path} ({size // 1024} KB; run artifact {relative}). It is a PNG; a "
        "teammate with image input can look at it. For structure and text, Browser Snapshot is "
        "cheaper and exact."
    )


# -- acting --------------------------------------------------------------------------------------


def click(session: PlaywrightSession, ref: str, double: bool, button: str) -> str:
    if button not in ("left", "right", "middle"):
        raise ToolError("button must be left, right, or middle.")
    target = locator(session, ref)
    try:
        if double:
            target.dblclick(button=button)
        else:
            target.click(button=button)
    except Exception as exc:
        raise translate(exc) from exc
    return _after(session, f"Clicked {ref}")


def type_text(session: PlaywrightSession, ref: str, text: str, clear: bool, submit: bool) -> str:
    if len(text) > MAX_TYPED_CHARS:
        raise ToolError(f"Type at most {MAX_TYPED_CHARS} characters at a time.")
    target = locator(session, ref)
    try:
        if clear:
            target.fill(text)
        else:
            target.press_sequentially(text)
        if submit:
            target.press("Enter")
    except Exception as exc:
        raise translate(exc) from exc
    return _after(
        session,
        f"Typed {len(text)} character(s) into {ref}{' and pressed Enter' if submit else ''}",
    )


def select(session: PlaywrightSession, ref: str, value: str) -> str:
    target = locator(session, ref)
    try:
        chosen = target.select_option(value)
    except Exception as exc:
        raise translate(exc) from exc
    return _after(session, f"Selected {chosen} in {ref}")


def press_key(session: PlaywrightSession, key: str, ref: str) -> str:
    if not key.strip():
        raise ToolError("Name a key such as Enter, Escape, Tab, ArrowDown, or Control+A.")
    try:
        if ref.strip():
            locator(session, ref).press(key)
        else:
            page_of(session).keyboard.press(key)
    except Exception as exc:
        raise translate(exc) from exc
    return _after(session, f"Pressed {key}")


def wait_for(
    session: PlaywrightSession,
    text: str,
    text_gone: str,
    seconds: float,
    state: str,
    timeout: float,
) -> str:
    page = page_of(session)
    limit = int(max(0.5, min(timeout, session.settings.page_timeout_seconds)) * 1000)
    try:
        if text:
            page.get_by_text(text).first.wait_for(state="visible", timeout=limit)
            return _after(session, f"The text {text!r} is visible")
        if text_gone:
            page.get_by_text(text_gone).first.wait_for(state="hidden", timeout=limit)
            return _after(session, f"The text {text_gone!r} is gone")
        if state:
            if state not in WAIT_STATES:
                raise ToolError(f"state must be one of: {', '.join(WAIT_STATES)}.")
            page.wait_for_load_state(state, timeout=limit)
            return _after(session, f"Page reached '{state}'")
        pause = max(0.0, min(float(seconds), MAX_WAIT_SECONDS))
        if pause <= 0:
            raise ToolError("Give text, text_gone, state, or seconds (up to 10) to wait for.")
        page.wait_for_timeout(int(pause * 1000))
        return _after(session, f"Waited {pause:g}s")
    except Exception as exc:
        raise translate(exc) from exc


def set_viewport(session: PlaywrightSession, preset: str, width: int, height: int) -> str:
    page = page_of(session)
    if preset:
        if preset not in VIEWPORTS:
            raise ToolError(
                f"preset must be one of: {', '.join(VIEWPORTS)} (or give width and height)."
            )
        width, height = VIEWPORTS[preset]
    if not (200 <= width <= 4000 and 200 <= height <= 4000):
        raise ToolError("width and height must be between 200 and 4000 pixels.")
    try:
        page.set_viewport_size({"width": int(width), "height": int(height)})
    except Exception as exc:
        raise translate(exc) from exc
    return _after(session, f"Viewport is now {int(width)}x{int(height)}")


# -- diagnostics ---------------------------------------------------------------------------------


def console_report(session: PlaywrightSession, kind: str, clear: bool) -> str:
    if kind not in ("all", "errors"):
        raise ToolError("kind must be 'all' or 'errors'.")
    counts = session.counts
    entries = [e for e in session.log if kind == "all" or e.is_problem]
    head = (
        f"{counts['console']} console message(s), {counts['pageerror']} uncaught error(s), "
        f"{counts['requestfailed']} failed request(s), {counts['http']} HTTP error response(s), "
        f"{counts['blocked']} request(s) refused by the navigation guard"
    )
    lines = [head]
    for entry in entries:
        where = f" ({entry.where})" if entry.where else ""
        lines.append(f"{entry.kind}/{entry.level}: {entry.text}{where}")
    if not entries:
        lines.append("Nothing to show." if kind == "all" else "No problems recorded.")
    if clear:
        session.clear_log()
        lines.append("(log cleared)")
    session.reported = session.problems
    return wrap_untrusted("the page's console and network log", "\n".join(lines))


def accessibility_check(session: PlaywrightSession) -> str:
    page = page_of(session)
    try:
        text = page.locator("body").aria_snapshot(mode="ai")
        raw = page.evaluate(scripts.PAGE_FACTS)
    except Exception as exc:
        raise translate(exc) from exc
    facts = DomFacts(
        lang=str(raw.get("lang", "")),
        title=str(raw.get("title", "")),
        contrast=[
            ContrastIssue(**{k: item[k] for k in ContrastIssue.__dataclass_fields__})
            for item in raw.get("contrast", [])
        ],
    )
    nodes = parse_snapshot(text)
    return wrap_untrusted(page.url, format_report(check(nodes, facts), nodes=len(nodes)))
