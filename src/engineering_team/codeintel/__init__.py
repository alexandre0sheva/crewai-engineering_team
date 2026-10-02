"""Code intelligence: where things are defined, who uses them, and what depends on what.

Everything here is static and best-effort (``ast`` for Python, regular expressions for the
other languages); the agent tools in :mod:`engineering_team.tools.codeintel_tools` wrap it.
"""

from __future__ import annotations

# The tool package imports this one, and this one imports the workspace helpers from that
# package. Loading the package first fixes the order, so importing any ``codeintel`` module
# on its own cannot hit a half-initialised ``engineering_team.tools``.
import engineering_team.tools  # noqa: F401
