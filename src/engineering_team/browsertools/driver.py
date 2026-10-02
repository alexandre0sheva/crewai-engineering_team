"""The Playwright driver: one headless browser per run, an incognito context per agent.

Created lazily on the registry's worker thread (Playwright's sync API is bound to it). Every
context gets the navigation guard as a route, downloads off, service workers blocked, no
permissions, no stored credentials (a fresh context has no cookies or storage), and a hard default
timeout. Page events (console, uncaught errors, failed or error responses, dialogs, popups,
refused requests) are collected per session for ``Browser Console & Errors``.
"""

from __future__ import annotations

import contextlib
import secrets
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from engineering_team.browsertools.guard import BlockedRequest, NavigationGuard
from engineering_team.browsertools.proxy import GuardProxy
from engineering_team.runtime.browsers import BrowserDriver, BrowserUnavailable
from engineering_team.runtime.events import EventSink
from engineering_team.settings import BrowserSettings, Settings

if TYPE_CHECKING:
    from engineering_team.runtime.processes import ProcessRegistry

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
LAUNCH_ARGS = [
    "--disable-extensions",
    "--disable-sync",
    "--disable-background-networking",  # no connectivity checks, updates, or sign-in traffic
    "--disable-component-update",
    "--disable-default-apps",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=AutofillServerCommunication,OptimizationHints,MediaRouter,Translate",
]
MAX_ENTRY_CHARS = 300
INSTALL_HINT = (
    "Install the extra and a browser: `uv sync --extra browser` then "
    '`uv run playwright install chromium`; or set browser.channel = "chrome" to use an '
    "installed Google Chrome (no download)."
)


@dataclass(frozen=True)
class LogEntry:
    kind: str  # console, pageerror, requestfailed, http, dialog, blocked
    level: str  # error, warning, info, log, ...
    text: str
    where: str = ""

    @property
    def is_problem(self) -> bool:
        return self.kind in ("pageerror", "requestfailed", "http", "blocked") or (
            self.kind == "console" and self.level == "error"
        )


def _clip(text: object) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= MAX_ENTRY_CHARS else value[: MAX_ENTRY_CHARS - 3] + "..."


class PlaywrightSession:
    """One agent's incognito context, its pages, and what happened on them."""

    def __init__(
        self,
        context: Any,
        agent: str,
        settings: BrowserSettings,
        guard: NavigationGuard,
        events: EventSink,
        screenshots: Path,
        proxy: GuardProxy,
        token: str,
    ) -> None:
        self.context = context
        self.agent = agent
        self.settings = settings
        self.guard = guard
        self.events = events
        self.screenshots_dir = screenshots
        self.page: Any = None
        self.log: deque[LogEntry] = deque(maxlen=settings.max_console_entries)
        self.problems = 0  # total problems seen since the last clear (not capped by the deque)
        self.reported = 0  # how many of them an action result already mentioned
        self.counts = {"console": 0, "pageerror": 0, "requestfailed": 0, "http": 0, "blocked": 0}
        self.shots = 0
        self.armed = False  # set by the first Browser Open: only page traffic is reported
        self._adopted: list[Any] = []
        self.refusals: list[BlockedRequest] = []  # everything the guard proxy refused
        self._proxy, self._token = proxy, token
        proxy.register(token, self._blocked)  # refusals by the guard proxy reach this session
        context.on("page", self._adopt)

    # -- pages -------------------------------------------------------------------------------

    def ensure_page(self) -> Any:
        if self.page is None or self.page.is_closed():
            self._adopt(self.context.new_page())
        return self.page

    def _adopt(self, page: Any) -> None:
        self.page = page  # a popup becomes the page the next tool acts on
        if page in self._adopted:
            return  # new_page() reports the page through the context event too
        self._adopted.append(page)
        page.on("console", self._console)
        page.on("pageerror", lambda error: self._add("pageerror", "error", str(error), page.url))
        page.on(
            "requestfailed",
            lambda request: self._add(
                "requestfailed",
                "error",
                f"{request.method} {_loggable(request.url)}: {request.failure}",
            ),
        )
        page.on("response", self._response)
        page.on("dialog", self._dialog)

    # -- collecting ------------------------------------------------------------------------------

    def _add(self, kind: str, level: str, text: object, where: str = "") -> None:
        entry = LogEntry(kind, level, _clip(text), _loggable(where))
        self.log.append(entry)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if entry.is_problem:
            self.problems += 1

    def _console(self, message: Any) -> None:
        location = message.location or {}
        where = f"{location.get('url', '')}:{location.get('lineNumber', '')}".rstrip(":")
        self._add("console", message.type, message.text, where)

    def _response(self, response: Any) -> None:
        if response.headers.get("x-browser-guard"):
            return  # a refusal by the guard proxy: recorded as "blocked", not as a server error
        if response.status >= 400:
            self._add(
                "http",
                "error",
                f"HTTP {response.status} {response.request.method} {_loggable(response.url)}",
            )

    def _dialog(self, dialog: Any) -> None:
        self._add("dialog", "info", f"{dialog.type} dialog dismissed: {dialog.message}")
        with contextlib.suppress(Exception):
            dialog.dismiss()

    def _blocked(self, blocked: BlockedRequest) -> None:
        if not self.armed or _browser_noise(blocked.url):
            return  # Chrome's own background traffic is refused too, but it is not news
        self.refusals.append(blocked)
        self._add("blocked", "error", f"blocked {blocked.url}: {blocked.reason}")
        self.events.emit(
            "browser.blocked", url=blocked.url, reason=blocked.reason, agent=self.agent
        )

    def clear_log(self) -> None:
        self.log.clear()
        self.counts = dict.fromkeys(self.counts, 0)
        self.problems = self.reported = 0

    def new_problems(self) -> int:
        fresh = self.problems - self.reported
        self.reported = self.problems
        return fresh

    # -- lifecycle ---------------------------------------------------------------------------------

    def close(self) -> None:
        self._proxy.unregister(self._token)
        with contextlib.suppress(Exception):  # the browser may already be gone
            self.context.close()


BROWSER_NOISE_HOSTS = (
    "content-autofill.googleapis.com",
    "optimizationguide-pa.googleapis.com",
    "update.googleapis.com",
    "clients2.google.com",
    "safebrowsing.googleapis.com",
)


def _browser_noise(url: str) -> bool:
    """Requests Chrome makes by itself (a search-engine preconnect, autofill, updates)."""

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host in BROWSER_NOISE_HOSTS or (host == "www.google.com" and parts.path in ("", "/"))


def _loggable(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


class PlaywrightDriver:
    """Starts Playwright and one browser on first use; hands out sessions."""

    def __init__(
        self, settings: Settings, guard: NavigationGuard, events: EventSink, run_dir: Path
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._events = events
        self._run_dir = run_dir
        self._playwright: Any = None
        self._browser: Any = None
        self._proxy = GuardProxy(guard)

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(f"Playwright is not installed. {INSTALL_HINT}") from exc
        self._proxy.start()
        self._playwright = sync_playwright().start()
        options = self._settings.browser
        launch: dict[str, Any] = {
            "headless": True,
            "args": LAUNCH_ARGS,
            "timeout": 60_000,
            "proxy": {"server": "per-context"},  # each context names the guard proxy itself
        }
        if options.channel != "chromium":
            launch["channel"] = options.channel
        try:
            self._browser = self._playwright.chromium.launch(**launch)
        except Exception as exc:
            self.close()
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                raise BrowserUnavailable(
                    f"No browser is installed for Playwright. {INSTALL_HINT}"
                ) from exc
            raise BrowserUnavailable(f"The browser could not start: {first}") from exc

    def new_session(self, agent: str) -> PlaywrightSession:
        options = self._settings.browser
        token = secrets.token_hex(16)
        context = self._browser.new_context(
            proxy={
                "server": f"http://127.0.0.1:{self._proxy.port}",
                "username": token,
                "password": "guard",
                "bypass": "<-loopback>",  # Chromium skips proxies for loopback unless told not to
            },
            viewport=DEFAULT_VIEWPORT,
            accept_downloads=False,
            service_workers="block",
            permissions=[],
            locale="en-US",
            ignore_https_errors=False,
        )
        context.set_default_timeout(options.page_timeout_seconds * 1000)
        context.set_default_navigation_timeout(options.page_timeout_seconds * 1000)
        screenshots = self._run_dir / "screenshots"
        return PlaywrightSession(
            context, agent, options, self._guard, self._events, screenshots, self._proxy, token
        )

    def close(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        self._proxy.stop()
        for closer in (
            getattr(browser, "close", None),
            getattr(playwright, "stop", None),
        ):
            if closer is not None:
                with contextlib.suppress(Exception):
                    closer()


def create_driver(
    settings: Settings, processes: ProcessRegistry, events: EventSink, run_dir: Path
) -> BrowserDriver:
    """The driver for a run: started on the worker thread, guarded by the run's own ports."""

    guard = NavigationGuard(
        is_run_port=processes.is_run_port,
        known_ports=processes.run_ports,
        web_enabled=settings.web.enabled,
        allow_domains=tuple(settings.web.allow_domains),
        deny_domains=tuple(settings.web.deny_domains),
    )
    driver = PlaywrightDriver(settings, guard, events, run_dir)
    driver.start()
    return driver


def playwright_installed() -> bool:
    """Whether the optional ``browser`` extra is installed (the tools exist only then)."""

    import importlib.util

    return importlib.util.find_spec("playwright") is not None


__all__ = ["PlaywrightDriver", "PlaywrightSession", "create_driver", "playwright_installed"]
