"""The one exception intake raises: a ``ValueError`` so the CLI reports it as a usage error."""

from __future__ import annotations


class IntakeError(ValueError):
    """A request or context directory that cannot be used; the message says how to fix it."""
