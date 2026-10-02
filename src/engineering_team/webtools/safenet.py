"""An HTTP client that cannot be talked into reaching private networks (SSRF-safe).

Rules, all enforced *before* a connection is made and again on every redirect hop:

* only ``http`` and ``https``; no user name or password in the URL; a valid port;
* names such as ``localhost``, ``*.local``, and ``*.internal`` are refused outright;
* the host name is resolved once, **every** answer must be a public address (loopback, private,
  link-local such as the cloud metadata address, carrier-grade NAT, multicast, unspecified,
  reserved, and IPv6 forms that wrap an IPv4 address are all refused), and the connection is made
  to the address that was validated: the name is never resolved a second time, so DNS rebinding
  cannot swap in a private address between the check and the connect;
* optional ``allow``/``deny`` host lists (deny wins; a non-empty allow list restricts);
* a per-run request cap, a total timeout, a body byte cap, and a content-type filter;
* no cookies and no ``Authorization`` header are ever carried across hops; compression is
  decoded with an output limit.

Proxies, environment variables, and ``requests``/``httpx`` redirect logic are not used.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from engineering_team.runtime.requests import RequestLimiter, RequestLimitReached
from engineering_team.tools.support import ToolError

Resolver = Callable[[str], list[str]]
Connector = Callable[[str, int, float], socket.socket]
TlsWrapper = Callable[[socket.socket, str], socket.socket]

USER_AGENT = "engineering-team/0.2 (+https://github.com/alexandre0sheva/crewai-engineering_team)"
DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5
MAX_HEADERS = 20
FORBIDDEN_HEADERS = frozenset({"host", "content-length", "connection", "transfer-encoding"})
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")
NAT64 = ipaddress.ip_network("64:ff9b::/96")


def system_resolver(host: str) -> list[str]:
    """Every address the system resolver returns for ``host`` (also numeric spellings)."""

    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def system_connector(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout=timeout)


def system_tls(sock: socket.socket, host: str) -> socket.socket:
    return ssl.create_default_context().wrap_socket(sock, server_hostname=host)


@dataclass(frozen=True)
class NetworkHooks:
    """How names are resolved and connections made. Tests swap :data:`NETWORK` for a fake net."""

    resolver: Resolver = system_resolver
    connector: Connector = system_connector
    wrap_tls: TlsWrapper = system_tls


NETWORK = NetworkHooks()


def _embedded_v4(address: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """The IPv4 addresses an IPv6 address stands for (mapped, 6to4, Teredo, NAT64)."""

    found = []
    if address.ipv4_mapped:
        found.append(address.ipv4_mapped)
    if address.sixtofour:
        found.append(address.sixtofour)
    if address.teredo:
        found.extend(address.teredo)
    if address in NAT64:
        found.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return found


def blocked_address(ip: str) -> str | None:
    """Why ``ip`` may not be contacted, or ``None`` when it is a public address."""

    try:
        address = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return "not a valid IP address"
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address):
        candidates += _embedded_v4(address)
    for item in candidates:
        if item.is_loopback:
            return "loopback"
        if item.is_link_local:
            return "link-local (includes the cloud metadata address)"
        if item.is_unspecified:
            return "unspecified"
        if item.is_multicast:
            return "multicast"
        if item.is_private:
            return "private network"
        if not item.is_global:
            return "not a public address"
    return None


@dataclass(frozen=True)
class DomainRules:
    """Optional host lists: exact names or ``*.suffix`` (subdomains only). Deny wins."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    @staticmethod
    def _match(host: str, patterns: tuple[str, ...]) -> bool:
        return any(
            host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
            for pattern in patterns
        )

    def check(self, host: str) -> str | None:
        if self._match(host, self.deny):
            return f"{host} is on the deny list (web.deny_domains)"
        if self.allow and not self._match(host, self.allow):
            return f"{host} is not on the allow list (web.allow_domains: {', '.join(self.allow)})"
        return None


@dataclass(frozen=True)
class WebHop:
    """One outbound request as the observer sees it (URL without its query string)."""

    method: str
    url: str
    status: int | None
    milliseconds: int
    address: str | None
    blocked: str | None = None


@dataclass
class WebResponse:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    truncated: bool
    url: str  # the final URL
    redirects: list[str]
    seconds: float

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        port = "" if self.port == DEFAULT_PORTS[self.scheme] else f":{self.port}"
        return f"{self.scheme}://{host}{port}{self.path}"

    @property
    def loggable(self) -> str:
        return self.url.split("?", 1)[0]


class _PinnedConnection(http.client.HTTPConnection):
    """Connects to an address that was already validated, never to the name."""

    def __init__(
        self,
        target: _Target,
        ip: str,
        timeout: float,
        connector: Connector,
        wrap_tls: TlsWrapper,
    ) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self._target, self._ip = target, ip
        self._connector, self._wrap_tls = connector, wrap_tls

    def connect(self) -> None:
        sock = self._connector(self._ip, self._target.port, self.timeout or 10.0)
        if self._target.scheme == "https":
            sock = self._wrap_tls(sock, self._target.host)  # certificate checked for the name
        self.sock = sock


class WebFetcher:
    """Fetches URLs under the rules in the module docstring."""

    def __init__(
        self,
        *,
        rules: DomainRules | None = None,
        limiter: RequestLimiter | None = None,
        observer: Callable[[WebHop], None] | None = None,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        resolver: Resolver | None = None,
        connector: Connector | None = None,
        wrap_tls: TlsWrapper | None = None,
    ) -> None:
        self._rules = rules or DomainRules()
        self._limiter = limiter
        self._observer = observer
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._resolver = resolver or NETWORK.resolver
        self._connector = connector or NETWORK.connector
        self._wrap_tls = wrap_tls or NETWORK.wrap_tls

    # -- public ------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        follow_redirects: bool = True,
        enforce_rules: bool = True,
        accept_types: tuple[str, ...] | None = None,
        max_bytes: int | None = None,
    ) -> WebResponse:
        """Call ``url``; raises :class:`ToolError` (with the fix) for anything refused."""

        method = method.upper()
        send = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate", **_clean(headers)}
        limit = max_bytes or self._max_bytes
        deadline = time.monotonic() + self._timeout
        started = time.monotonic()
        redirects: list[str] = []
        next_url = url
        while True:
            target, ip = self._admit(method, next_url, enforce_rules)
            response = self._send(method, target, ip, send, body, deadline, limit, accept_types)
            location = response.headers.get("location")
            if follow_redirects and response.status in REDIRECT_STATUSES and location:
                if len(redirects) >= MAX_REDIRECTS:
                    raise ToolError(
                        f"More than {MAX_REDIRECTS} redirects; the last went to {location}."
                    )
                redirects.append(f"{response.status} -> {location}")
                next_url = urljoin(target.url, location)
                if response.status in (301, 302, 303) and method not in ("GET", "HEAD"):
                    method, body = "GET", None
                continue
            response.redirects = redirects
            response.seconds = time.monotonic() - started
            return response

    # -- admission: every refusal happens here, before a connection ---------------------------

    def _admit(self, method: str, url: str, enforce_rules: bool) -> tuple[_Target, str]:
        target = _parse(url)
        try:
            ip = self._vet(target.host, enforce_rules)
        except ToolError as exc:
            self._observe(WebHop(method, target.loggable, None, 0, None, str(exc)))
            raise
        return target, ip

    def _vet(self, host: str, enforce_rules: bool) -> str:
        if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
            raise ToolError(
                f"Blocked: {host} is a local name. Only public internet hosts can be fetched."
            )
        if enforce_rules and (reason := self._rules.check(host)):
            raise ToolError(f"Blocked: {reason}.")
        literal = _literal_ip(host)
        if literal is not None:
            addresses = [literal]
        else:
            try:
                addresses = self._resolver(host.encode("idna").decode("ascii"))
            except (OSError, UnicodeError) as exc:
                raise ToolError(f"Could not resolve host {host}: {exc}.") from exc
        if not addresses:
            raise ToolError(f"Could not resolve host {host}: no addresses.")
        for address in addresses:
            if (reason := blocked_address(address)) is not None:
                raise ToolError(
                    f"Blocked: {host} resolves to {address} ({reason}). Only public internet "
                    "addresses can be fetched; private, loopback, and metadata addresses never can."
                )
        return addresses[0]

    # -- one hop -----------------------------------------------------------------------------

    def _observe(self, hop: WebHop) -> None:
        if self._observer is not None:
            self._observer(hop)

    def _send(
        self,
        method: str,
        target: _Target,
        ip: str,
        headers: Mapping[str, str],
        body: bytes | None,
        deadline: float,
        limit: int,
        accept_types: tuple[str, ...] | None,
    ) -> WebResponse:
        if self._limiter is not None:
            try:
                self._limiter.acquire()
            except RequestLimitReached as exc:
                raise ToolError(str(exc)) from exc
        remaining = max(0.5, deadline - time.monotonic())
        connection = _PinnedConnection(target, ip, remaining, self._connector, self._wrap_tls)
        started = time.monotonic()
        status: int | None = None
        try:
            connection.request(method, target.path, body=body, headers=dict(headers))
            raw = connection.getresponse()
            status = raw.status
            received = {key.lower(): value for key, value in raw.getheaders()}
            kind = received.get("content-type", "").split(";")[0].strip().lower()
            is_redirect = status in REDIRECT_STATUSES and "location" in received
            if accept_types and not is_redirect and not kind.startswith(accept_types):
                raise ToolError(
                    f"{target.loggable} returned {kind or 'no content type'}, which this tool "
                    f"does not read (accepted: {', '.join(accept_types)})."
                )
            data = _read_limited(raw, limit + 1, deadline)
            data, truncated = _decode(data, received.get("content-encoding", ""), limit)
            return WebResponse(status, raw.reason, received, data, truncated, target.url, [], 0.0)
        except TimeoutError as exc:
            raise ToolError(f"{method} {target.loggable} timed out.") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ToolError(f"Could not reach {target.loggable}: {exc}") from exc
        finally:
            connection.close()
            self._observe(
                WebHop(
                    method,
                    target.loggable,
                    status,
                    round((time.monotonic() - started) * 1000),
                    ip,
                )
            )


# -- helpers ---------------------------------------------------------------------------------


def _literal_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _parse(url: str) -> _Target:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ToolError(f"Not a valid URL ({exc}): {url!r}") from exc
    if parts.scheme not in DEFAULT_PORTS:
        raise ToolError("Only http:// and https:// URLs can be fetched.")
    if parts.username or parts.password:
        raise ToolError("URLs with a user name or password are not allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ToolError(f"The URL has no host: {url!r}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return _Target(parts.scheme, host, port or DEFAULT_PORTS[parts.scheme], path)


def _clean(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    if len(headers) > MAX_HEADERS:
        raise ToolError(f"At most {MAX_HEADERS} headers may be sent.")
    clean = {}
    for name, value in headers.items():
        if str(name).lower() in FORBIDDEN_HEADERS:
            raise ToolError(f"The {name} header is set by the tool and cannot be overridden.")
        if any(char in f"{name}{value}" for char in "\r\n"):
            raise ToolError("Header names and values cannot contain line breaks.")
        clean[str(name)] = str(value)
    return clean


def _read_limited(raw: http.client.HTTPResponse, limit: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        if time.monotonic() > deadline:
            raise TimeoutError
        chunk = raw.read(min(65536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _decode(data: bytes, encoding: str, limit: int) -> tuple[bytes, bool]:
    """Decompress (gzip/deflate) with an output cap, then apply the byte cap."""

    kind = encoding.strip().lower()
    if kind in ("gzip", "x-gzip", "deflate"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if "gzip" in kind else zlib.MAX_WBITS)
        try:
            data = decoder.decompress(data, limit + 1)
        except zlib.error as exc:
            raise ToolError(f"The response was not valid {kind} data ({exc}).") from exc
    elif kind not in ("", "identity"):
        raise ToolError(f"Unsupported content encoding {kind!r}.")
    return data[:limit], len(data) > limit
