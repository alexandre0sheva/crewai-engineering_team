"""Search providers over the fake network: request shape, parsing, and key handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_fakes import FakeNet

from engineering_team.settings import Settings, load_settings
from engineering_team.tools.support import ToolError
from engineering_team.webtools.safenet import WebFetcher
from engineering_team.webtools.search import (
    BraveProvider,
    SearchResult,
    SerperProvider,
    TavilyProvider,
    format_results,
    select_provider,
)

PUBLIC = "93.184.216.34"
KEY = "key-0123456789abcdef"
JSON = {"Content-Type": "application/json"}

SERPER = {
    "organic": [
        {
            "title": "Fastify docs",
            "link": "https://fastify.dev/docs",
            "snippet": "Fast web framework.",
        },
        {"title": "Bad scheme", "link": "javascript:alert(1)", "snippet": "x"},
        {"title": "Second", "link": "https://example.org/two", "snippet": "Two " + "word " * 200},
    ]
}
BRAVE = {
    "web": {
        "results": [
            {
                "title": "Python <strong>docs</strong>",
                "url": "https://docs.python.org/3/",
                "description": "The <strong>official</strong> docs &amp; tutorial.",
            }
        ]
    }
}
TAVILY = {
    "results": [{"title": "Tavily hit", "url": "https://t.example/x", "content": "Body text."}]
}


@pytest.fixture
def net() -> FakeNet:
    fake = FakeNet(
        hosts={
            "google.serper.dev": [PUBLIC],
            "api.search.brave.com": [PUBLIC],
            "api.tavily.com": [PUBLIC],
        }
    )
    fake.routes[("google.serper.dev", "/search")] = (200, JSON, json.dumps(SERPER).encode())
    fake.routes[("api.search.brave.com", "/res/v1/web/search")] = (
        200,
        JSON,
        json.dumps(BRAVE).encode(),
    )
    fake.routes[("api.tavily.com", "/search")] = (200, JSON, json.dumps(TAVILY).encode())
    return fake


@pytest.fixture
def fetcher(net: FakeNet) -> WebFetcher:
    return WebFetcher(resolver=net.resolver, connector=net.connector, wrap_tls=net.wrap_tls)


def test_serper_posts_the_query_with_the_key_in_a_header(fetcher: WebFetcher, net: FakeNet) -> None:
    results = SerperProvider(KEY).search(fetcher, "fastify routing", 5)

    request = net.seen[0]
    assert (request.method, request.path) == ("POST", "/search")
    assert json.loads(request.body) == {"q": "fastify routing", "num": 5}
    assert request.headers["x-api-key"] == KEY
    assert KEY not in request.path
    assert [r.title for r in results] == [
        "Fastify docs",
        "Second",
    ]  # the javascript: link is dropped
    assert results[0] == SearchResult(
        "Fastify docs", "https://fastify.dev/docs", "Fast web framework."
    )
    assert len(results[1].snippet) <= 300


def test_brave_uses_a_get_with_the_token_header_and_strips_markup(
    fetcher: WebFetcher, net: FakeNet
) -> None:
    results = BraveProvider(KEY).search(fetcher, "python docs", 3)

    request = net.seen[0]
    assert request.method == "GET" and request.path.startswith("/res/v1/web/search?q=python")
    assert "count=3" in request.path and KEY not in request.path
    assert request.headers["x-subscription-token"] == KEY
    assert results == [
        SearchResult("Python docs", "https://docs.python.org/3/", "The official docs & tutorial.")
    ]


def test_tavily_posts_json_with_a_bearer_token(fetcher: WebFetcher, net: FakeNet) -> None:
    results = TavilyProvider(KEY).search(fetcher, "q", 4)

    request = net.seen[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.body)["max_results"] == 4
    assert results == [SearchResult("Tavily hit", "https://t.example/x", "Body text.")]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (429, "rate limit"),
        (500, "HTTP 500"),
    ],
)
def test_provider_errors_are_explained_without_the_key(
    fetcher: WebFetcher, net: FakeNet, status: int, message: str
) -> None:
    net.routes[("google.serper.dev", "/search")] = (status, JSON, b'{"message": "no"}')

    with pytest.raises(ToolError, match=message) as error:
        SerperProvider(KEY).search(fetcher, "q", 3)

    assert KEY not in str(error.value)
    if status in (401, 403):
        assert "SERPER_API_KEY" in str(error.value)


def test_a_non_json_answer_is_an_error(fetcher: WebFetcher, net: FakeNet) -> None:
    net.routes[("google.serper.dev", "/search")] = (200, JSON, b"<html>captive portal</html>")

    with pytest.raises(ToolError, match="not valid JSON"):
        SerperProvider(KEY).search(fetcher, "q", 3)


def test_results_are_compact_and_numbered() -> None:
    text = format_results(
        "serper",
        "q",
        [SearchResult("A", "https://a.test/", "alpha"), SearchResult("B", "https://b.test/", "")],
    )

    assert text.splitlines()[0] == "2 result(s) for 'q' (serper)"
    assert "1. A\n   https://a.test/\n   alpha" in text
    assert "2. B\n   https://b.test/" in text
    assert format_results("serper", "q", []).startswith("No results for 'q'")


def _settings(tmp_path: Path, env: dict[str, str], **web: object) -> Settings:
    overrides = {f"web.{key}": value for key, value in web.items()}
    return load_settings(env=env, cwd=tmp_path, home=tmp_path / "home", overrides=overrides)


def test_the_provider_is_chosen_by_setting_else_by_the_first_key_present(tmp_path: Path) -> None:
    both = {"BRAVE_API_KEY": KEY, "TAVILY_API_KEY": KEY}

    assert select_provider(_settings(tmp_path, both)).name == "brave"
    assert select_provider(_settings(tmp_path, both, search_provider="tavily")).name == "tavily"
    assert select_provider(_settings(tmp_path, {"SERPER_API_KEY": KEY})).name == "serper"


def test_a_missing_key_names_the_variable_to_set_never_a_value(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="SERPER_API_KEY, BRAVE_API_KEY, or TAVILY_API_KEY"):
        select_provider(_settings(tmp_path, {}))
    with pytest.raises(ToolError, match="BRAVE_API_KEY"):
        select_provider(_settings(tmp_path, {"SERPER_API_KEY": KEY}, search_provider="brave"))
