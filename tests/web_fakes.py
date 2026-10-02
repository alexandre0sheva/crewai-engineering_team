"""An in-memory internet for the web tool tests: fake DNS plus canned HTTP servers.

Nothing here opens a real socket to another host. ``FakeNet.connector`` hands the client one
end of a ``socketpair`` and serves a canned response on the other end, recording which IP and
port the client asked for, so tests can prove *where* a request would have gone.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

Route = tuple[int, dict[str, str], bytes]


@dataclass
class Seen:
    ip: str
    port: int
    host: str
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeNet:
    hosts: dict[str, list[str]] = field(default_factory=dict)  # name -> A/AAAA answers
    routes: dict[tuple[str, str], Route | Callable[[Seen], Route]] = field(default_factory=dict)
    resolutions: list[str] = field(default_factory=list)
    connections: list[tuple[str, int]] = field(default_factory=list)
    seen: list[Seen] = field(default_factory=list)
    tls_hosts: list[str] = field(default_factory=list)

    def page(
        self, host: str, path: str, body: bytes | str, *, status: int = 200, **headers: str
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.routes[(host, path)] = (status, {"Content-Type": "text/html", **headers}, data)

    # -- the client's hooks ------------------------------------------------------------

    def resolver(self, host: str) -> list[str]:
        self.resolutions.append(host)
        if host not in self.hosts:
            raise OSError(f"no such host: {host}")
        return list(self.hosts[host])

    def connector(self, ip: str, port: int, timeout: float) -> socket.socket:
        self.connections.append((ip, port))
        client, server = socket.socketpair()
        client.settimeout(timeout)
        threading.Thread(target=self._serve, args=(server, ip, port), daemon=True).start()
        return client

    def wrap_tls(self, sock: socket.socket, host: str) -> socket.socket:
        self.tls_hosts.append(host)
        return sock

    # -- the canned server -------------------------------------------------------------

    def _serve(self, server: socket.socket, ip: str, port: int) -> None:
        try:
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = server.recv(65536)
                if not chunk:
                    return
                raw += chunk
            head, _, rest = raw.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
            wanted = int(headers.get("content-length", "0"))
            while len(rest) < wanted:
                rest += server.recv(65536)
            host = headers.get("host", "").split(":")[0]
            seen = Seen(ip, port, host, method, path, headers, rest)
            self.seen.append(seen)
            route = self.routes.get((host, path.split("?")[0]))
            if route is None:
                route = (404, {"Content-Type": "text/plain"}, b"not found")
            status, response_headers, body = route(seen) if callable(route) else route
            out = [f"HTTP/1.1 {status} X"]
            out += [f"{name}: {value}" for name, value in response_headers.items()]
            if "Content-Length" not in response_headers:
                out.append(f"Content-Length: {len(body)}")
            server.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + body)
        except OSError:
            pass
        finally:
            server.close()
