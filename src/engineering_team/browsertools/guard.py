"""The navigation guard: which URLs the browser may load, for pages and every sub-request.

By default only ``localhost``, ``127.0.0.1``, and ``[::1]`` **on ports this run started or
reserved** (so a page cannot be pointed at another local service). Other sites need
``web.enabled`` *and* a ``web.allow_domains`` entry (``web.deny_domains`` wins); an allowlisted
name that resolves to a private, link-local, or metadata address is still refused. ``file://``,
``chrome://``, ``view-source:``, ``javascript:``, ``data:`` pages, and credentials in a URL are
never allowed. The guard decides; :mod:`~engineering_team.browsertools.proxy` enforces it on
every request the browser makes (redirect hops, sub-resources, tunnels, and WebSockets) and
connects allowlisted external hosts to the address it validated.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from engineering_team.webtools import safenet
from engineering_team.webtools.safenet import DomainRules, blocked_address

LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_PORTS = {"http": 80, "https": 443}
LOCAL_SUFFIXES = (".local", ".internal")


@dataclass(frozen=True)
class BlockedRequest:
    url: str  # without query string or fragment
    reason: str


def _loggable(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


class NavigationGuard:
    """Decides, per URL, whether the browser may load it (see the module docstring)."""

    def __init__(
        self,
        *,
        is_run_port: Callable[[int], bool],
        known_ports: Callable[[], Mapping[int, str]],
        web_enabled: bool = False,
        allow_domains: tuple[str, ...] = (),
        deny_domains: tuple[str, ...] = (),
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._is_run_port = is_run_port
        self._known_ports = known_ports
        self._web_enabled = web_enabled
        self._allow = allow_domains
        self._deny = deny_domains
        self._resolver = resolver

    def check(self, url: str) -> str | None:
        """Why ``url`` may not be loaded, or ``None`` when it may."""

        text = url.strip()
        if text == "about:blank":
            return None
        try:
            parts = urlsplit(text)
            port = parts.port
        except ValueError as exc:
            return f"Not a valid URL ({exc})."
        if parts.scheme not in DEFAULT_PORTS:
            return (
                f"Only http(s) pages can be opened, not '{parts.scheme or text[:20]}:' URLs "
                "(file://, data:, chrome://, javascript: and the like are never allowed)."
            )
        if parts.username or parts.password:
            return "URLs with a user name or password are not allowed."
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            return "The URL has no host."
        port = port or DEFAULT_PORTS[parts.scheme]
        if self._is_loopback(host):
            return self._check_loopback(host, port)
        return self._check_external(host)

    # -- loopback: this run's own ports ------------------------------------------------------

    @staticmethod
    def _is_loopback(host: str) -> bool:
        if host in LOOPBACK_NAMES or host.endswith(".localhost"):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _check_loopback(self, host: str, port: int) -> str | None:
        if self._is_run_port(port):
            return None
        known = ", ".join(str(p) for p in sorted(self._known_ports())) or "none yet"
        return (
            f"Port {port} on {host} is not one of this run's ports (known: {known}). Start the "
            "app with Start Background Process (ports=...) or reserve a port with Find Free Port."
        )

    # -- external sites: only through the web settings --------------------------------------------

    def _check_external(self, host: str) -> str | None:
        if not self._web_enabled:
            return (
                f"{host} is an external site and the web tools are off (web.enabled = false), so "
                "the browser only opens this run's own localhost ports."
            )
        if host.endswith(LOCAL_SUFFIXES):
            return f"{host} is a local name and is never allowed."
        rules = DomainRules(allow=self._allow, deny=self._deny)
        if reason := rules.check(host):
            return f"{reason}."
        if not self._allow:
            return f"{host} is not allowed: web.allow_domains is empty, so no external site is."
        literal = _literal(host)
        try:
            addresses = [literal] if literal else (self._resolver or safenet.NETWORK.resolver)(host)
        except OSError as exc:
            return f"Could not resolve {host}: {exc}."
        for address in addresses:
            if (why := blocked_address(address)) is not None:
                return f"{host} resolves to {address} ({why}); private addresses are never allowed."
        return None


def _literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None
