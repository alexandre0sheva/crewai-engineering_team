"""A filtering proxy every browser request goes through, so the guard sees every hop.

Why a proxy: Playwright's request interception does not see the hops of a redirect the browser
follows itself, nor (by default) WebSockets, so a page on an allowed port could bounce the browser
to a service the guard refuses. With the browser's whole network stack pointed at this proxy,
*each* request (redirect hops, sub-resources, and ``CONNECT`` tunnels for https and WebSockets) is
checked by :class:`~engineering_team.browsertools.guard.NavigationGuard` before anything is
contacted.

* It listens on ``127.0.0.1`` on a free port and serves only requests carrying a session's token
  (HTTP Basic ``Proxy-Authorization``; Chromium sends it after a 407), so other local processes
  cannot use it.
* Refused requests get a ``403`` that says why, and are reported to the session that made them.
* For non-loopback hosts (only reachable through ``web.allow_domains``) it resolves the name
  itself, requires every answer to be a public address, and connects to the validated address, so
  the address cannot be swapped between the check and the connect.
* It never follows redirects (the browser does, as new requests), forwards one request per
  connection (``Connection: close``), and relays everything else as bytes, so streaming,
  cookies, and ``Upgrade`` (WebSocket) traffic work unchanged.
"""

from __future__ import annotations

import base64
import contextlib
import socket
import socketserver
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from engineering_team.browsertools.guard import BlockedRequest, NavigationGuard
from engineering_team.webtools import safenet
from engineering_team.webtools.safenet import blocked_address

MAX_HEAD_BYTES = 65_536
IDLE_SECONDS = 120.0
CONNECT_SECONDS = 15.0
LINGER_SECONDS = 2.0
REASONS = {
    400: "Bad Request",
    403: "Forbidden",
    407: "Proxy Authentication Required",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
}


class _Refused(Exception):
    """A connect-time refusal (the name now resolves to a non-public address)."""


def _display(host: str, port: int, scheme: str, path: str = "/") -> str:
    shown = f"[{host}]" if ":" in host else host
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{shown}{'' if port == default else f':{port}'}{path.split('?', 1)[0]}"


class GuardProxy:
    """The run's filtering proxy (see the module docstring)."""

    def __init__(
        self,
        guard: NavigationGuard,
        *,
        resolver: Callable[[str], list[str]] | None = None,
        connector: Callable[[str, int, float], socket.socket] | None = None,
    ) -> None:
        self._guard = guard
        self._resolver = resolver
        self._connector = connector
        self._sessions: dict[str, Callable[[BlockedRequest], None]] = {}
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        proxy = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    proxy._serve(self.request)
                except OSError:
                    pass  # a browser that hangs up mid-request is not an error
                finally:
                    with contextlib.suppress(OSError):
                        self.request.close()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05) if self._server else None,
            name="browser-guard-proxy",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def register(self, token: str, on_block: Callable[[BlockedRequest], None]) -> None:
        with self._lock:
            self._sessions[token] = on_block

    def unregister(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    # -- one connection ---------------------------------------------------------------------------

    def _serve(self, client: socket.socket) -> None:
        client.settimeout(IDLE_SECONDS)
        read = _read_head(client)
        if read is None:
            return _reply(client, 431, "The request head is too large or incomplete.")
        head, rest = read
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            return _reply(client, 400, "Not an HTTP proxy request.")
        method, target = parts[0].upper(), parts[1]
        headers = [tuple(line.split(":", 1)) for line in lines[1:] if ":" in line]
        on_block = self._authenticate(headers)
        if on_block is None:
            return _reply(client, 407, "", extra='Proxy-Authenticate: Basic realm="guard"\r\n')
        try:
            if method == "CONNECT":
                host, port = _authority(target)
                scheme, path = "https", "/"
            else:
                url = urlsplit(target)
                if url.scheme != "http" or not url.hostname:
                    return _reply(client, 400, "Only absolute http:// URLs can be proxied.")
                host, port = url.hostname.lower(), url.port or 80
                scheme = "http"
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        except ValueError:
            return _reply(client, 400, "Bad request target.")
        shown = _display(host, port, scheme, path)
        reason = self._guard.check(
            _display(host, port, scheme, "/") if method == "CONNECT" else target
        )
        if reason is None:
            try:
                upstream = self._connect(host, port)
            except _Refused as refusal:
                reason = str(refusal)
            except OSError as exc:
                return _reply(client, 502, f"Could not reach {host}:{port}: {exc}")
        if reason is not None:
            on_block(BlockedRequest(shown, reason))
            return _reply(
                client,
                403,
                f"Blocked by the browser guard: {reason}",
                extra=f"{GUARD_HEADER}: blocked\r\n",
            )
        with upstream:
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                _relay(client, upstream, rest)
            else:
                upstream.sendall(_forward_head(method, path, headers) + rest)
                _relay(client, upstream, b"")

    def _authenticate(
        self, headers: list[tuple[str, ...]]
    ) -> Callable[[BlockedRequest], None] | None:
        for name, value in headers:
            if name.strip().lower() == "proxy-authorization":
                scheme, _, encoded = value.strip().partition(" ")
                if scheme.lower() != "basic":
                    return None
                try:
                    token = base64.b64decode(encoded.strip()).decode().split(":", 1)[0]
                except ValueError:
                    return None
                with self._lock:
                    return self._sessions.get(token)
        return None

    def _connect(self, host: str, port: int) -> socket.socket:
        if NavigationGuard._is_loopback(host):
            return socket.create_connection((host, port), timeout=CONNECT_SECONDS)
        resolver = self._resolver or safenet.NETWORK.resolver
        connector = self._connector or safenet.NETWORK.connector
        addresses = resolver(host)
        for address in addresses:
            if (why := blocked_address(address)) is not None:
                raise _Refused(
                    f"{host} resolves to {address} ({why}); private addresses are never allowed"
                )
        if not addresses:
            raise OSError(f"no addresses for {host}")
        return connector(addresses[0], port, CONNECT_SECONDS)  # the validated address, not the name


# -- wire helpers --------------------------------------------------------------------------------


def _read_head(sock: socket.socket) -> tuple[bytes, bytes] | None:
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEAD_BYTES:
            return None
        chunk = sock.recv(8192)
        if not chunk:
            return None
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return (head, rest) if len(head) <= MAX_HEAD_BYTES else None


def _authority(target: str) -> tuple[str, int]:
    """``host:port`` or ``[v6]:port`` of a CONNECT target."""

    host, _, port = target.rpartition(":")
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(target)
    return host.strip("[]").lower(), int(port)


def _forward_head(method: str, path: str, headers: list[tuple[str, ...]]) -> bytes:
    upgrade = any(
        name.strip().lower() == "connection" and "upgrade" in value.lower()
        for name, value in headers
    )
    lines = [f"{method} {path} HTTP/1.1"]
    for name, value in headers:
        lowered = name.strip().lower()
        if lowered.startswith("proxy-") or (lowered == "connection" and not upgrade):
            continue
        lines.append(f"{name.strip()}:{value}")
    if not upgrade:
        lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


GUARD_HEADER = "X-Browser-Guard"


def _reply(client: socket.socket, status: int, message: str, extra: str = "") -> None:
    body = message.encode()
    head = (
        f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n{extra}Connection: close\r\n\r\n"
    ).encode()
    try:
        client.sendall(head + body)
        client.shutdown(socket.SHUT_WR)
        client.settimeout(0.3)
        for _ in range(64):  # drain what the client was still sending so the reply is not reset
            if not client.recv(65536):
                break
    except OSError:
        pass


def _pipe(source: socket.socket, sink: socket.socket, done: threading.Event) -> None:
    try:
        while chunk := source.recv(65536):
            sink.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            sink.shutdown(socket.SHUT_WR)
        done.set()


def _relay(client: socket.socket, upstream: socket.socket, pending: bytes) -> None:
    """Copy bytes both ways until one side finishes, then give the other a moment and close."""

    upstream.settimeout(IDLE_SECONDS)
    if pending:
        upstream.sendall(pending)
    done = threading.Event()
    threads = [
        threading.Thread(target=_pipe, args=(client, upstream, done), daemon=True),
        threading.Thread(target=_pipe, args=(upstream, client, done), daemon=True),
    ]
    for thread in threads:
        thread.start()
    done.wait()
    for thread in threads:
        thread.join(timeout=LINGER_SECONDS)
    for sock in (client, upstream):
        with contextlib.suppress(OSError):
            sock.close()
