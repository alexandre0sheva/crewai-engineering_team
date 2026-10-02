"""The SSRF-safe client: what it refuses *before connecting*, and where it connects."""

from __future__ import annotations

import gzip
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from web_fakes import FakeNet

from engineering_team.runtime.requests import RequestLimiter
from engineering_team.tools.support import ToolError
from engineering_team.webtools.safenet import (
    DomainRules,
    WebFetcher,
    WebHop,
    blocked_address,
    system_resolver,
)

PUBLIC = "93.184.216.34"
Make = Callable[..., WebFetcher]


@pytest.fixture
def net() -> FakeNet:
    fake = FakeNet(hosts={"example.test": [PUBLIC], "docs.example.test": [PUBLIC]})
    fake.page("example.test", "/", "<p>hello</p>")
    return fake


@pytest.fixture
def make(net: FakeNet) -> Make:
    def build(**options: object) -> WebFetcher:
        options.setdefault("resolver", net.resolver)
        return WebFetcher(connector=net.connector, wrap_tls=net.wrap_tls, **options)  # type: ignore[arg-type]

    return build


# -- the address rules ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%en0",
        "fd00::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "not-an-ip",
    ],
)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert blocked_address(address) is not None


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert blocked_address(address) is None


# -- refusals happen before any connection -------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
        "http://localhost/",
        "http://app.localhost/",
        "http://printer.local/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_private_targets_are_refused_without_connecting(make: Make, net: FakeNet, url: str) -> None:
    with pytest.raises(ToolError, match="(?i)blocked|not allowed"):
        make().request("GET", url)

    assert net.connections == []


def test_a_hostname_that_resolves_to_a_private_address_is_refused(make: Make, net: FakeNet) -> None:
    net.hosts["evil.test"] = ["10.0.0.5"]
    net.hosts["meta.test"] = ["169.254.169.254"]
    net.hosts["loop.test"] = ["::1"]

    for host, shown in (("evil.test", "10.0.0.5"), ("meta.test", "169.254.169.254")):
        with pytest.raises(ToolError, match=f"{host}.*{shown}"):
            make().request("GET", f"http://{host}/")
    with pytest.raises(ToolError, match="loop.test"):
        make().request("GET", "http://loop.test/")

    assert net.connections == []


def test_one_private_answer_among_public_ones_refuses_the_host(make: Make, net: FakeNet) -> None:
    net.hosts["mixed.test"] = [PUBLIC, "192.168.0.10"]

    with pytest.raises(ToolError, match="192.168.0.10"):
        make().request("GET", "http://mixed.test/")

    assert net.connections == []


def test_numeric_host_spellings_resolve_to_loopback_and_are_blocked() -> None:
    """The real system resolver turns ``2130706433`` and ``127.1`` into 127.0.0.1 (no network)."""

    connected: list[object] = []
    fetcher = WebFetcher(
        resolver=system_resolver, connector=lambda *a: connected.append(a) or socket.socket()
    )

    # (The resolver reads "0177.0.0.1" as the public 177.0.0.1 and the client connects to exactly
    # what it validated, so the spellings that matter are the ones that resolve to loopback.)
    for url in ("http://2130706433/", "http://127.1/", "http://0x7f000001/"):
        with pytest.raises(ToolError, match="(?i)blocked"):
            fetcher.request("GET", url)

    assert connected == []


def test_dns_rebinding_cannot_swap_the_address_after_the_check(make: Make, net: FakeNet) -> None:
    """The first answer is validated *and used*: a second, private answer is never consulted."""

    answers = iter([[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]])
    net.resolver = lambda host: (net.resolutions.append(host), next(answers))[1]  # type: ignore[method-assign]

    response = make(resolver=net.resolver).request("GET", "http://example.test/")

    assert response.status == 200
    assert net.connections == [(PUBLIC, 80)]
    assert net.resolutions == ["example.test"]  # resolved once, then pinned
    assert net.seen[0].host == "example.test"  # the Host header still names the site


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/",
        "gopher://example.test/",
        "javascript:alert(1)",
        "//example.test/",
        "http://",
        "http://user:pass@example.test/",
        "http://example.test:99999/",
        "example.test/no-scheme",
    ],
)
def test_only_plain_http_and_https_urls_are_accepted(make: Make, net: FakeNet, url: str) -> None:
    with pytest.raises(ToolError):
        make().request("GET", url)

    assert net.connections == []


def test_redirects_to_private_addresses_are_refused_on_every_hop(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/go")] = (302, {"Location": "http://169.254.169.254/"}, b"")
    net.routes[("example.test", "/loop")] = (302, {"Location": "http://127.0.0.1:9/"}, b"")
    net.routes[("example.test", "/dns")] = (302, {"Location": "http://rebind.test/"}, b"")
    net.hosts["rebind.test"] = ["10.9.9.9"]

    for path in ("/go", "/loop", "/dns"):
        with pytest.raises(ToolError, match="(?i)blocked"):
            make().request("GET", f"http://example.test{path}")

    assert set(net.connections) == {(PUBLIC, 80)}  # only the first hop ever connected


def test_redirects_between_public_hosts_are_followed_and_reported(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/old")] = (301, {"Location": "http://docs.example.test/new"}, b"")
    net.page("docs.example.test", "/new", "<p>moved</p>")

    response = make().request("GET", "http://example.test/old")

    assert response.url == "http://docs.example.test/new"
    assert response.body == b"<p>moved</p>"
    assert response.redirects == ["301 -> http://docs.example.test/new"]


def test_too_many_redirects_stop(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/a")] = (302, {"Location": "/a"}, b"")

    with pytest.raises(ToolError, match="redirects"):
        make().request("GET", "http://example.test/a")


def test_redirects_can_be_turned_off(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/old")] = (302, {"Location": "http://example.test/"}, b"")

    response = make().request("GET", "http://example.test/old", follow_redirects=False)

    assert response.status == 302


# -- domain lists ------------------------------------------------------------------------


def test_deny_beats_allow_and_a_non_empty_allow_list_restricts(make: Make, net: FakeNet) -> None:
    rules = DomainRules(allow=("example.test", "*.example.test"), deny=("docs.example.test",))

    assert make(rules=rules).request("GET", "http://example.test/").status == 200
    with pytest.raises(ToolError, match="deny"):
        make(rules=rules).request("GET", "http://docs.example.test/new")
    with pytest.raises(ToolError, match="allow"):
        make(rules=rules).request("GET", "http://other.test/")


def test_domain_rules_apply_to_redirect_targets_but_provider_calls_can_skip_them(
    make: Make, net: FakeNet
) -> None:
    net.routes[("example.test", "/out")] = (302, {"Location": "http://docs.example.test/new"}, b"")
    net.page("docs.example.test", "/new", "ok")
    rules = DomainRules(allow=("example.test",))

    with pytest.raises(ToolError, match="allow"):
        make(rules=rules).request("GET", "http://example.test/out")
    skipped = make(rules=rules).request("GET", "http://docs.example.test/new", enforce_rules=False)
    assert skipped.status == 200


def test_domain_rules_match_exact_names_and_wildcards() -> None:
    rules = DomainRules(allow=("a.test", "*.b.test"), deny=("x.b.test",))

    assert rules.check("a.test") is None and rules.check("y.b.test") is None
    assert rules.check("b.test") is not None  # the wildcard is for subdomains only
    assert "deny" in (rules.check("x.b.test") or "")
    assert DomainRules().check("anything.test") is None


# -- caps ----------------------------------------------------------------------------------


def test_bodies_are_cut_at_the_byte_cap(make: Make, net: FakeNet) -> None:
    net.page("example.test", "/big", b"x" * 5000)

    response = make(max_bytes=1000).request("GET", "http://example.test/big")

    assert len(response.body) == 1000 and response.truncated


def test_gzip_bodies_are_decoded_within_the_cap(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/z")] = (
        200,
        {"Content-Type": "text/plain", "Content-Encoding": "gzip"},
        gzip.compress(b"a" * 100_000),
    )

    response = make(max_bytes=2000).request("GET", "http://example.test/z")

    assert len(response.body) == 2000 and response.truncated


def test_content_types_outside_the_allowed_set_are_refused(make: Make, net: FakeNet) -> None:
    net.page("example.test", "/bin", b"\x00\x01", **{"Content-Type": "application/octet-stream"})

    with pytest.raises(ToolError, match="application/octet-stream"):
        make().request("GET", "http://example.test/bin", accept_types=("text/", "application/json"))
    ok = make().request("GET", "http://example.test/", accept_types=("text/", "application/json"))
    assert ok.status == 200


def test_the_request_cap_counts_every_hop_and_is_shared(make: Make, net: FakeNet) -> None:
    net.routes[("example.test", "/old")] = (302, {"Location": "http://example.test/"}, b"")
    limiter = RequestLimiter(3)
    fetcher = make(limiter=limiter)

    fetcher.request("GET", "http://example.test/old")  # two hops
    fetcher.request("GET", "http://example.test/")
    with pytest.raises(ToolError, match="request limit"):
        fetcher.request("GET", "http://example.test/")

    assert limiter.used == 3


def test_a_blocked_request_does_not_use_up_the_cap(make: Make) -> None:
    limiter = RequestLimiter(1)

    with pytest.raises(ToolError):
        make(limiter=limiter).request("GET", "http://127.0.0.1/")

    assert limiter.used == 0


def test_https_connects_to_the_pinned_ip_and_verifies_the_original_name(
    make: Make, net: FakeNet
) -> None:
    net.page("example.test", "/s", "secure")

    make().request("GET", "https://example.test/s")

    assert net.connections == [(PUBLIC, 443)]
    assert net.tls_hosts == ["example.test"]


def test_every_hop_is_reported_without_its_query_string(make: Make, net: FakeNet) -> None:
    hops: list[WebHop] = []
    net.page("example.test", "/q", "ok")

    make(observer=hops.append).request("GET", "http://example.test/q?token=SECRET")
    with pytest.raises(ToolError):
        make(observer=hops.append).request("GET", "http://127.0.0.1/x?token=SECRET")

    assert [(h.method, h.url, h.status, h.blocked) for h in hops] == [
        ("GET", "http://example.test/q", 200, None),
        ("GET", "http://127.0.0.1/x", None, hops[1].blocked),
    ]
    assert hops[1].blocked and "SECRET" not in repr(hops)


def test_headers_cannot_smuggle_lines_or_override_the_host(make: Make) -> None:
    with pytest.raises(ToolError, match="line breaks"):
        make().request("GET", "http://example.test/", headers={"X-A": "b\r\nHost: evil"})
    with pytest.raises(ToolError, match="Host"):
        make().request("GET", "http://example.test/", headers={"Host": "evil.test"})


# -- the real default connector: a loopback server must never be reached ----------------------


def test_a_real_loopback_server_is_never_contacted() -> None:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"secret")

        def log_message(self, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        for host in ("127.0.0.1", "localhost"):
            with pytest.raises(ToolError, match="(?i)blocked|not allowed"):
                WebFetcher().request("GET", f"http://{host}:{port}/admin")
    finally:
        server.shutdown()
        server.server_close()

    assert hits == []
