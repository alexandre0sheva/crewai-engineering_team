"""The browser's navigation guard: what it allows and refuses, tested without a browser."""

from __future__ import annotations

import pytest

from engineering_team.browsertools.guard import NavigationGuard

PUBLIC = "93.184.216.34"


def make_guard(
    *,
    run_ports: tuple[int, ...] = (5173,),
    web_enabled: bool = False,
    allow: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
    hosts: dict[str, list[str]] | None = None,
) -> NavigationGuard:
    resolved = hosts or {}

    def resolver(host: str) -> list[str]:
        if host not in resolved:
            raise OSError("no such host")
        return resolved[host]

    return NavigationGuard(
        is_run_port=lambda port: port in run_ports,
        known_ports=lambda: {port: "dev server" for port in run_ports},
        web_enabled=web_enabled,
        allow_domains=allow,
        deny_domains=deny,
        resolver=resolver,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173/",
        "http://127.0.0.1:5173/app?x=1",
        "http://[::1]:5173/",
        "http://LOCALHOST:5173/index.html#top",
    ],
)
def test_this_runs_own_loopback_ports_are_allowed(url: str) -> None:
    assert make_guard().check(url) is None


@pytest.mark.parametrize(
    "url", ["http://localhost:8000/", "http://127.0.0.1/", "https://localhost/", "http://[::1]:22/"]
)
def test_other_loopback_ports_are_refused_with_the_fix(url: str) -> None:
    reason = make_guard().check(url)

    assert reason and "not one of this run's ports" in reason
    assert "Start Background Process" in reason and "5173" in reason


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://localhost:5173/",
        "javascript:alert(1)",
        "view-source:http://localhost:5173/",
        "chrome://settings",
        "chrome-extension://abc/page.html",
        "about:config",
        "data:text/html,<h1>x</h1>",
        "blob:http://localhost:5173/abc",
        "http://user:pass@localhost:5173/",
        "http://",
        "not a url",
    ],
)
def test_other_schemes_and_odd_urls_are_refused(url: str) -> None:
    assert make_guard().check(url) is not None


def test_about_blank_is_the_one_allowed_non_http_page() -> None:
    assert make_guard().check("about:blank") is None


def test_external_hosts_are_refused_unless_web_is_enabled_and_the_host_is_allowlisted() -> None:
    hosts = {"docs.example.test": [PUBLIC], "cdn.example.test": [PUBLIC]}

    off = make_guard(allow=("docs.example.test",), hosts=hosts)
    on_unlisted = make_guard(web_enabled=True, hosts=hosts)
    on = make_guard(web_enabled=True, allow=("docs.example.test", "*.cdn.test"), hosts=hosts)

    assert "web tools are off" in (off.check("https://docs.example.test/") or "")
    assert "web.allow_domains" in (on_unlisted.check("https://docs.example.test/") or "")
    assert on.check("https://docs.example.test/page") is None
    assert on.check("https://cdn.example.test/lib.js") is not None  # not in the allow list


def test_wildcard_allow_entries_cover_subdomains_only() -> None:
    guard = make_guard(
        web_enabled=True,
        allow=("*.example.test",),
        hosts={"a.example.test": [PUBLIC], "example.test": [PUBLIC]},
    )

    assert guard.check("https://a.example.test/") is None
    assert guard.check("https://example.test/") is not None


def test_deny_beats_allow() -> None:
    guard = make_guard(
        web_enabled=True,
        allow=("*.example.test",),
        deny=("bad.example.test",),
        hosts={"bad.example.test": [PUBLIC]},
    )

    assert "deny list" in (guard.check("https://bad.example.test/") or "")


def test_an_allowlisted_name_that_resolves_privately_is_still_refused() -> None:
    guard = make_guard(
        web_enabled=True,
        allow=("intranet.example.test", "meta.example.test"),
        hosts={
            "intranet.example.test": ["10.0.0.7"],
            "meta.example.test": [PUBLIC, "169.254.169.254"],
        },
    )

    assert "10.0.0.7" in (guard.check("https://intranet.example.test/") or "")
    assert "169.254.169.254" in (guard.check("https://meta.example.test/") or "")


def test_allowlisting_private_ip_literals_does_not_open_them() -> None:
    guard = make_guard(web_enabled=True, allow=("10.0.0.5", "169.254.169.254", "*.internal"))

    assert guard.check("http://10.0.0.5/") is not None
    assert guard.check("http://169.254.169.254/latest/") is not None
    assert guard.check("http://metadata.internal/") is not None
