"""Per-run caps shared by every agent of the run: web requests, and model calls per minute."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


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


class RateLimiter:
    """A requests-per-minute cap shared by every agent of a run (a sliding 60-second window).

    CrewAI agents call ``check_or_wait()`` before each model call when one is installed
    (``agent.set_rpm_controller``), so one limiter makes parallel agents share the cap that a
    per-crew ``max_rpm`` would apply to each crew alone. Waiting wakes at once when the run is
    cancelled.
    """

    def __init__(
        self,
        max_rpm: int,
        cancel_event: threading.Event | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        window: float = 60.0,
    ) -> None:
        self.max_rpm = max_rpm
        self._cancel = cancel_event
        self._clock = clock
        self._window = window
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()
        self.calls = 0  # how many calls took a slot
        self.waits = 0  # how many of them had to wait

    def check_or_wait(self) -> bool:
        """Take a slot, waiting until the window has room. Always returns ``True``."""

        waited = False
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_rpm:
                    self._stamps.append(now)
                    self.calls += 1
                    if waited:
                        self.waits += 1
                    return True
                delay = self._window - (now - self._stamps[0])
            waited = True
            if self._cancel is not None:
                if self._cancel.wait(min(delay, 0.5)):
                    return True  # cancelled: let the call through, the tools refuse the rest
            else:
                time.sleep(min(delay, 0.5))
