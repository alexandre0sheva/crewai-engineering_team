"""Failures a retry cannot fix (a model that cannot call tools will not do better the second
time), so the stage and work-package retry loops let them through at once."""

from __future__ import annotations


class NoRetry(RuntimeError):
    """Base for a failure that is final: it is recorded, and the stage or package fails now."""
