"""Web Search providers: Serper, Brave, and Tavily behind one small interface.

Results are title, URL, and a short snippet, nothing else. The API key goes in a request header
(never the URL, so it cannot reach a log), is read from the provider's environment variable by
:class:`~engineering_team.settings.Settings`, and never appears in a message.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote_plus

from engineering_team.settings import SEARCH_PROVIDERS, WEB_KEY_ENV, Settings
from engineering_team.tools.support import ToolError
from engineering_team.webtools.safenet import WebFetcher, WebResponse

MAX_SNIPPET_CHARS = 300
MAX_TITLE_CHARS = 150
MAX_RESULTS = 10
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    def search(self, fetcher: WebFetcher, query: str, count: int) -> list[SearchResult]:
        """Up to ``count`` results for ``query``; raises :class:`ToolError` with the fix."""


def _clean(text: object, limit: int) -> str:
    value = html.unescape(_TAGS.sub("", str(text or "")))
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _results(items: object, title: str, url: str, snippet: str, count: int) -> list[SearchResult]:
    found: list[SearchResult] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        link = str(item.get(url) or "").strip()
        if not link.lower().startswith(("http://", "https://")):
            continue  # javascript:, data:, and friends are never offered to an agent
        found.append(
            SearchResult(
                _clean(item.get(title), MAX_TITLE_CHARS) or link,
                link,
                _clean(item.get(snippet), MAX_SNIPPET_CHARS),
            )
        )
    return found[:count]


def _call(
    name: str,
    fetcher: WebFetcher,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> Any:
    response: WebResponse = fetcher.request(
        method,
        url,
        headers={**headers, "Accept": "application/json"},
        body=json.dumps(body).encode() if body is not None else None,
        follow_redirects=False,
        enforce_rules=False,  # a fixed provider host, still checked for public addresses
        accept_types=("application/json",),
    )
    variable = WEB_KEY_ENV[name]
    if response.status in (401, 403):
        raise ToolError(
            f"The {name} search provider rejected the API key. Check the {variable} environment "
            "variable (and the plan it belongs to)."
        )
    if response.status == 429:
        raise ToolError(
            f"The {name} search provider hit its rate limit; wait a bit or search less."
        )
    if response.status >= 400:
        raise ToolError(f"The {name} search provider answered HTTP {response.status}.")
    try:
        return json.loads(response.body)
    except ValueError as exc:
        raise ToolError(f"The {name} search provider's answer was not valid JSON.") from exc


def _count(count: int) -> int:
    return max(1, min(int(count), MAX_RESULTS))


class SerperProvider:
    name = "serper"
    endpoint = "https://google.serper.dev/search"

    def __init__(self, key: str) -> None:
        self._key = key

    def search(self, fetcher: WebFetcher, query: str, count: int) -> list[SearchResult]:
        count = _count(count)
        data = _call(
            self.name,
            fetcher,
            "POST",
            self.endpoint,
            {"X-API-KEY": self._key, "Content-Type": "application/json"},
            {"q": query, "num": count},
        )
        items = data.get("organic") if isinstance(data, dict) else None
        return _results(items, "title", "link", "snippet", count)


class BraveProvider:
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, key: str) -> None:
        self._key = key

    def search(self, fetcher: WebFetcher, query: str, count: int) -> list[SearchResult]:
        count = _count(count)
        url = f"{self.endpoint}?q={quote_plus(query)}&count={count}"
        data = _call(self.name, fetcher, "GET", url, {"X-Subscription-Token": self._key}, None)
        web = data.get("web") if isinstance(data, dict) else None
        items = web.get("results") if isinstance(web, dict) else None
        return _results(items, "title", "url", "description", count)


class TavilyProvider:
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, key: str) -> None:
        self._key = key

    def search(self, fetcher: WebFetcher, query: str, count: int) -> list[SearchResult]:
        count = _count(count)
        data = _call(
            self.name,
            fetcher,
            "POST",
            self.endpoint,
            {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            {"query": query, "max_results": count},
        )
        items = data.get("results") if isinstance(data, dict) else None
        return _results(items, "title", "url", "content", count)


PROVIDERS: dict[str, Callable[[str], SearchProvider]] = {
    "serper": SerperProvider,
    "brave": BraveProvider,
    "tavily": TavilyProvider,
}


def select_provider(settings: Settings) -> SearchProvider:
    """The configured provider, else the first of serper, brave, tavily that has a key."""

    names = [settings.web.search_provider] if settings.web.search_provider else SEARCH_PROVIDERS
    for name in names:
        key = settings.web_api_key(name) if name else None
        if name and key:
            return PROVIDERS[name](key)
    wanted = settings.web.search_provider
    variables = [WEB_KEY_ENV[wanted]] if wanted else [WEB_KEY_ENV[n] for n in SEARCH_PROVIDERS]
    raise ToolError(
        f"No search API key is configured. Set {', '.join(variables[:-1])}"
        f"{', or ' if len(variables) > 1 else ''}{variables[-1]} in the environment "
        "(web.search_provider chooses the provider). Web Search is unavailable; use Fetch URL "
        "on a known page instead."
    )


def format_results(provider: str, query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"No results for '{query}' ({provider}). Try different or fewer keywords."
    lines = [f"{len(results)} result(s) for '{query}' ({provider})"]
    for number, result in enumerate(results, start=1):
        lines.append(f"{number}. {result.title}")
        lines.append(f"   {result.url}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
    lines.append("Open a result with Fetch URL.")
    return "\n".join(lines)
