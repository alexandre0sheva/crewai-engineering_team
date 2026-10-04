"""The server's request guard: who may call it and how large a request may be.

A pure ASGI middleware, so streaming responses (SSE) pass through untouched. In order:

1. **Host check** (local mode): only ``localhost``, ``127.0.0.1`` and ``[::1]`` are served, so a
   web page cannot reach the API through a hostname it controls (DNS rebinding).
2. **No cross-origin requests**: a request whose ``Origin`` is another host, or whose
   ``Sec-Fetch-Site`` is ``cross-site``, is refused. The server sends no CORS headers at all.
3. **Custom header on mutating calls**: ``X-Engineering-Team: 1``. A browser cannot add it to a
   cross-origin request without a preflight, which the server never approves.
4. **Bearer token** when the server listens beyond localhost (``--allow-remote``).
5. **Body size**: ``Content-Length`` and the bytes actually received are capped (a larger upload
   cap for ``multipart/form-data``, which is how files arrive).

``/health`` and the app shell (``/`` and ``/static/``: HTML, CSS and JavaScript, nothing
about any run) skip the token check (and only that), so a browser can load the page, find out it
needs a token, and then send it with every API call.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CUSTOM_HEADER = "x-engineering-team"
OPEN_PATHS = ("/health",)
SHELL_PREFIXES = ("/static/",)
MULTIPART_OVERHEAD = 64 * 1024  # form boundaries and field names around the files


@dataclass(frozen=True)
class Security:
    """How the server is exposed. ``token`` is required exactly when ``remote`` is true."""

    remote: bool = False
    token: str | None = None
    max_request_bytes: int = 200_000
    max_upload_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if self.remote and not self.token:
            raise ValueError("A server that accepts non-local connections needs a token.")


class _TooLarge(Exception):
    pass


def hostname_of(host_header: str) -> str:
    """``localhost`` from ``localhost:8765``; ``[::1]`` from ``[::1]:8765``."""

    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


class GuardMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], security: Security):
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        refusal = self._refusal(scope, headers)
        if refusal is not None:
            status, message = refusal
            await _reply(send, status, message)
            return
        limit = self._limit(headers)
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await _reply(send, 413, f"The request body is larger than the {limit}-byte limit.")
            return
        if scope["method"] in MUTATING and "transfer-encoding" in headers and declared is None:
            await _reply(send, 411, "Send a Content-Length (chunked bodies are not accepted).")
            return
        received = 0

        async def counted() -> MutableMapping[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        started = False

        async def tracked(message: MutableMapping[str, Any]) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except _TooLarge:
            if not started:
                await _reply(send, 413, f"The request body is larger than the {limit}-byte limit.")

    def _limit(self, headers: dict[str, str]) -> int:
        if headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return self.security.max_upload_bytes + MULTIPART_OVERHEAD
        return self.security.max_request_bytes

    def _refusal(self, scope: Scope, headers: dict[str, str]) -> tuple[int, str] | None:
        security = self.security
        host = headers.get("host", "")
        if not security.remote and hostname_of(host) not in LOCAL_HOSTS:
            return 403, "This server answers only on localhost."
        origin = headers.get("origin")
        if origin is not None and (
            origin == "null" or urlsplit(origin).netloc.lower() != host.strip().lower()
        ):
            return 403, "Cross-origin requests are not accepted."
        if headers.get("sec-fetch-site") == "cross-site":
            return 403, "Cross-site requests are not accepted."
        if scope["method"] in MUTATING and headers.get(CUSTOM_HEADER) != "1":
            return 403, "Mutating calls need the header 'X-Engineering-Team: 1'."
        if security.remote and not _open(scope):
            given = headers.get("authorization", "")
            scheme, _, secret = given.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                secret.strip().encode(), (security.token or "").encode()
            ):
                return 401, "A bearer token is required (Authorization: Bearer <token>)."
        return None


def _open(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    if scope["method"] != "GET":
        return False
    return path == "/" or path.startswith(SHELL_PREFIXES) or path.endswith(OPEN_PATHS)


async def _reply(send: Send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
