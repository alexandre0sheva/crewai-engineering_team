"""A per-run cap on outbound web requests, shared by every agent of the run."""

from __future__ import annotations

import threading


class RequestLimitReached(Exception):
    """The run has used all the web requests it is allowed."""


class RequestLimiter:
    """Counts outbound requests (every redirect hop counts) against a fixed limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def acquire(self) -> None:
        with self._lock:
            if self._used >= self.limit:
                raise RequestLimitReached(
                    f"This run has reached its web request limit ({self.limit}, setting "
                    "web.max_requests_per_run). Work from what you have already fetched."
                )
            self._used += 1
