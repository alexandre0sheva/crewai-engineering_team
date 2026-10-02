"""Opt-in web tools: SSRF-safe fetching, search providers, package registries, local docs.

Everything that leaves the machine goes through
:class:`~engineering_team.webtools.safenet.WebFetcher`; everything an agent reads from here is
wrapped as untrusted content.
"""

from __future__ import annotations

# See ``engineering_team.codeintel``: load the tool package first so the two import cleanly in
# either order.
import engineering_team.tools  # noqa: F401
