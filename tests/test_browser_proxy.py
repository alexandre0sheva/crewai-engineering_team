"""The guard proxy: every request the browser makes, hop by hop, over real local sockets."""

from __future__ import annotations

import base64
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engineering_team.browsertools.guard import BlockedRequest, NavigationGuard
from engineering_team.browsertools.proxy import GuardProxy

TOKEN = "t0k3n-0123456789"
PUBLIC = "93.184.216.34"


class Upstream:
    """A local server that counts every request it receives."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits.append(self.path)
                if self.path == "/redirect-out":
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = f"hello from {self.server.server_address[1]}{self.path}".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                data = self.rfile.read(size)
                outer.hits.append(f"POST {self.path} {data.decode()}")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                return None

        self.redirect_to = ""
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    server = Upstream()
    yield server
    server.close()


def make_proxy(
    allowed: tuple[int, ...], blocked: list[BlockedRequest], **guard: object
) -> GuardProxy:
    navigation = NavigationGuard(
        is_run_port=lambda port: port in allowed,
        known_ports=lambda: dict.fromkeys(allowed, "app"),
        **guard,  # type: ignore[arg-type]
    )
    proxy = GuardProxy(navigation)
    proxy.register(TOKEN, blocked.append)
    proxy.start()
    return proxy


def talk(
    proxy: GuardProxy, raw: bytes, *, token: str | None = TOKEN, read_all: bool = True
) -> bytes:
    """Send ``raw`` to the proxy (with Proxy-Authorization) and return everything it answers."""

    if token is not None:
        auth = base64.b64encode(f"{token}:".encode()).decode()
        raw = raw.replace(b"\r\n\r\n", f"\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode(), 1)
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
        sock.sendall(raw)
        sock.shutdown(socket.SHUT_WR) if not read_all else None
        out = b""
        while chunk := sock.recv(65536):
            out += chunk
    return out


def get(proxy: GuardProxy, url: str, **kwargs: object) -> bytes:
    host = url.split("/")[2]
    return talk(proxy, f"GET {url} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode(), **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def proxies() -> Iterator[list[GuardProxy]]:
    started: list[GuardProxy] = []
    yield started
    for proxy in started:
        proxy.stop()


def test_a_request_to_this_runs_own_port_is_forwarded(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    proxy = make_proxy((upstream.port,), [])
    proxies.append(proxy)

    answer = get(proxy, f"http://127.0.0.1:{upstream.port}/page?x=1")

    assert answer.startswith(b"HTTP/1.0 200") or answer.startswith(b"HTTP/1.1 200")
    assert f"hello from {upstream.port}/page?x=1".encode() in answer
    assert upstream.hits == ["/page?x=1"]


def test_post_bodies_are_relayed(upstream: Upstream, proxies: list[GuardProxy]) -> None:
    proxy = make_proxy((upstream.port,), [])
    proxies.append(proxy)
    request = (
        f"POST http://127.0.0.1:{upstream.port}/submit HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        "Content-Length: 5\r\n\r\nhello"
    ).encode()

    answer = talk(proxy, request)

    assert answer.endswith(b"hello") and upstream.hits == ["POST /submit hello"]


def test_other_local_ports_are_refused_and_never_contacted(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    blocked: list[BlockedRequest] = []
    proxy = make_proxy((1,), blocked)  # the upstream's port is not this run's
    proxies.append(proxy)

    answer = get(proxy, f"http://127.0.0.1:{upstream.port}/secret?token=SECRET")

    assert answer.startswith(b"HTTP/1.1 403")
    assert b"not one of this run's ports" in answer
    assert upstream.hits == []
    assert [b.url for b in blocked] == [f"http://127.0.0.1:{upstream.port}/secret"]
    assert "SECRET" not in repr(blocked)


def test_every_redirect_hop_is_a_new_request_through_the_guard(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    other = Upstream()
    try:
        upstream.redirect_to = f"http://127.0.0.1:{other.port}/internal"
        blocked: list[BlockedRequest] = []
        proxy = make_proxy((upstream.port,), blocked)
        proxies.append(proxy)

        first = get(proxy, f"http://127.0.0.1:{upstream.port}/redirect-out")
        location = next(
            line.split(b": ", 1)[1].decode()
            for line in first.split(b"\r\n")
            if line.lower().startswith(b"location")
        )
        second = get(proxy, location)  # what the browser does with the 302

        assert first.startswith(b"HTTP/1.0 302") or first.startswith(b"HTTP/1.1 302")
        assert second.startswith(b"HTTP/1.1 403")
        assert other.hits == [] and len(blocked) == 1
    finally:
        other.close()


def test_connect_tunnels_to_allowed_ports_and_refuses_the_rest(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    blocked: list[BlockedRequest] = []
    proxy = make_proxy((upstream.port,), blocked)
    proxies.append(proxy)
    auth = base64.b64encode(f"{TOKEN}:".encode()).decode()

    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
        sock.sendall(
            f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
        )
        reply = sock.recv(1024)
        sock.sendall(b"GET /tunnelled HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    refused = talk(proxy, b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
    external = talk(proxy, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")

    assert reply.startswith(b"HTTP/1.1 200")
    assert b"hello from" in data and "/tunnelled" in upstream.hits
    assert refused.startswith(b"HTTP/1.1 403") and external.startswith(b"HTTP/1.1 403")
    assert [b.url for b in blocked] == ["https://127.0.0.1:1/", "https://example.test/"]


def test_requests_without_the_run_token_are_not_served(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    proxy = make_proxy((upstream.port,), [])
    proxies.append(proxy)

    anonymous = get(proxy, f"http://127.0.0.1:{upstream.port}/", token=None)
    wrong = get(proxy, f"http://127.0.0.1:{upstream.port}/", token="not-the-token")

    assert anonymous.startswith(b"HTTP/1.1 407") and b"Proxy-Authenticate" in anonymous
    assert wrong.startswith(b"HTTP/1.1 407")
    assert upstream.hits == []


def test_a_session_that_closed_is_no_longer_served(
    upstream: Upstream, proxies: list[GuardProxy]
) -> None:
    proxy = make_proxy((upstream.port,), [])
    proxies.append(proxy)
    proxy.unregister(TOKEN)

    assert get(proxy, f"http://127.0.0.1:{upstream.port}/").startswith(b"HTTP/1.1 407")
    assert upstream.hits == []


@pytest.mark.parametrize(
    "raw",
    [
        b"GET /relative HTTP/1.1\r\nHost: x\r\n\r\n",
        b"GET ftp://127.0.0.1/ HTTP/1.1\r\nHost: x\r\n\r\n",
        b"GET file:///etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n",
        b"nonsense\r\n\r\n",
        b"GET http://127.0.0.1/ HTTP/1.1\r\n" + b"X-Pad: " + b"a" * 70_000 + b"\r\n\r\n",
    ],
)
def test_malformed_and_oversized_requests_are_rejected(
    upstream: Upstream, proxies: list[GuardProxy], raw: bytes
) -> None:
    proxy = make_proxy((upstream.port,), [])
    proxies.append(proxy)

    answer = talk(proxy, raw)

    assert answer.startswith((b"HTTP/1.1 400", b"HTTP/1.1 403", b"HTTP/1.1 431"))
    assert upstream.hits == []


def test_upgrade_requests_are_tunnelled_in_both_directions(proxies: list[GuardProxy]) -> None:
    received: list[bytes] = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            received.append(head)
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
            conn.sendall(b"server-says-hi")
            data = conn.recv(100)
            received.append(data)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    proxy = make_proxy((port,), [])
    proxies.append(proxy)
    auth = base64.b64encode(f"{TOKEN}:".encode()).decode()
    request = (
        f"GET http://127.0.0.1:{port}/ws HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: Upgrade\r\n"
        f"Upgrade: websocket\r\nProxy-Authorization: Basic {auth}\r\n\r\n"
    ).encode()

    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
        sock.sendall(request)
        seen = b""
        while b"server-says-hi" not in seen:
            seen += sock.recv(4096)
        sock.sendall(b"client-says-hi")
        worker.join(timeout=5)
    server.close()

    assert seen.startswith(b"HTTP/1.1 101") and b"server-says-hi" in seen
    assert b"Upgrade: websocket" in received[0] and b"Proxy-Authorization" not in received[0]
    assert received[1] == b"client-says-hi"


def test_external_hosts_connect_to_the_validated_address_only(proxies: list[GuardProxy]) -> None:
    connected: list[tuple[str, int]] = []
    lookups: list[str] = []
    answers = iter(
        [[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]]
    )  # rebinding: public first, then private

    def resolver(host: str) -> list[str]:
        lookups.append(host)
        return next(answers)

    def connector(ip: str, port: int, timeout: float) -> socket.socket:
        connected.append((ip, port))
        raise OSError("no network in tests")

    guard = NavigationGuard(
        is_run_port=lambda p: False,
        known_ports=dict,
        web_enabled=True,
        allow_domains=("docs.example.test",),
        resolver=lambda host: [PUBLIC],  # the guard's own lookup
    )
    proxy = GuardProxy(guard, resolver=resolver, connector=connector)
    proxy.register(TOKEN, lambda blocked: None)
    proxy.start()
    proxies.append(proxy)

    answer = talk(proxy, b"CONNECT docs.example.test:443 HTTP/1.1\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 502")  # the connect failed, but only to the public address
    assert connected == [(PUBLIC, 443)] and lookups == ["docs.example.test"]  # resolved once


def test_an_allowlisted_name_that_resolves_privately_is_refused_by_the_proxy(
    proxies: list[GuardProxy],
) -> None:
    connected: list[object] = []
    guard = NavigationGuard(
        is_run_port=lambda p: False,
        known_ports=dict,
        web_enabled=True,
        allow_domains=("docs.example.test",),
        resolver=lambda host: [PUBLIC],  # the guard's own lookup is clean ...
    )
    proxy = GuardProxy(
        guard,
        resolver=lambda host: ["10.0.0.9"],  # ... but the connect-time lookup is private
        connector=lambda *a: connected.append(a) or socket.socket(),
    )
    proxy.register(TOKEN, lambda blocked: None)
    proxy.start()
    proxies.append(proxy)

    answer = talk(proxy, b"CONNECT docs.example.test:443 HTTP/1.1\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 403") and b"10.0.0.9" in answer
    assert connected == []


def test_stopping_closes_the_port(upstream: Upstream) -> None:
    proxy = make_proxy((upstream.port,), [])
    port = proxy.port

    proxy.stop()
    proxy.stop()  # idempotent

    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
