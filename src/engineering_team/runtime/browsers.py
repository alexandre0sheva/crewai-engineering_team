"""The run's browsers: one worker thread, one context per agent, closed with their stage or run.

Playwright's synchronous API belongs to the thread that started it, while agents call tools from
whatever thread CrewAI uses. :class:`BrowserRegistry` therefore owns a single worker thread; every
browser operation is a function submitted to it and run there, one at a time. The driver (the
Playwright launcher, created lazily on the worker) hands out one *session* (an incognito context)
per agent, at most ``browser.max_contexts`` at once. Sessions are closed when the stage they were
opened in ends, and everything is closed, and the worker stopped, when the run ends, fails, or
crashes (an ``atexit`` hook is the last safety net). Nothing is started until the first use.
"""

from __future__ import annotations

import atexit
import contextlib
import queue
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Protocol, TypeVar

from engineering_team.runtime.events import EventSink, current_stage

T = TypeVar("T")
JOIN_SECONDS = 15.0
POLL_SECONDS = 0.25


class BrowserUnavailable(Exception):
    """The browser cannot be used (missing, closed, cancelled); the message says what to do."""


class BrowserLimitReached(Exception):
    """``browser.max_contexts`` browsers are already open."""


class BrowserSession(Protocol):
    def close(self) -> None: ...


class BrowserDriver(Protocol):
    def new_session(self, agent: str) -> BrowserSession: ...

    def close(self) -> None: ...


_registries: weakref.WeakSet[BrowserRegistry] = weakref.WeakSet()


@atexit.register
def _close_every_registry() -> None:
    for registry in list(_registries):
        registry.close_all("the interpreter is exiting")


class BrowserRegistry:
    """Owns the run's browser worker thread and the per-agent sessions on it."""

    def __init__(
        self,
        *,
        max_contexts: int,
        driver_factory: Callable[[], BrowserDriver],
        cancel_event: threading.Event,
        events: EventSink,
        name: str = "run",
    ) -> None:
        self._max_contexts = max_contexts
        self._driver_factory = driver_factory
        self._cancel = cancel_event
        self._events = events
        self._name = name
        self._lock = threading.Lock()
        self._jobs: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False
        # Touched only on the worker thread, except reads of ``open_agents`` (under the lock).
        self._driver: BrowserDriver | None = None
        self._sessions: dict[str, tuple[BrowserSession, str | None]] = {}
        _registries.add(self)

    # -- public ------------------------------------------------------------------------------

    def run(self, agent: str, operation: Callable[[Any], T], *, timeout: float = 90.0) -> T:
        """Run ``operation(session)`` on the worker with ``agent``'s session (created if needed)."""

        stage = current_stage.get()
        return self._submit(lambda: operation(self._session(agent, stage)), timeout)

    @property
    def worker_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def open_agents(self) -> list[str]:
        with self._lock:
            return sorted(self._sessions)

    def close_agent(self, agent: str) -> bool:
        """Close ``agent``'s session; ``False`` when it had none."""

        if not self._started():
            return False
        return self._submit(lambda: self._close_session(agent, "closed by the agent"), 30.0)

    def stop_stage(self, stage: str) -> None:
        """Close the sessions opened while ``stage`` was running."""

        if self._started():
            self._submit(
                lambda: self._close_where(lambda s: s == stage, f"stage {stage} ended"), 30.0
            )

    def close_all(self, reason: str) -> None:
        """Close every session, the browser, and the worker thread. Safe to call repeatedly."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is None:
            return
        done: Future[Any] = Future()
        self._jobs.put((lambda: self._shutdown(reason), done))
        self._jobs.put(None)
        with contextlib.suppress(Exception):  # shutting down must never raise into the run
            done.result(timeout=JOIN_SECONDS)
        thread.join(timeout=JOIN_SECONDS)

    # -- the worker thread ---------------------------------------------------------------------

    def _started(self) -> bool:
        with self._lock:
            return self._thread is not None and not self._closed

    def _worker(self) -> None:
        while (job := self._jobs.get()) is not None:
            operation, future = job
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(operation())
            except BaseException as exc:
                future.set_exception(exc)

    def _submit(self, operation: Callable[[], T], timeout: float) -> T:
        with self._lock:
            if self._closed:
                raise BrowserUnavailable("The browser was closed because the run has ended.")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker, name=f"browser-{self._name}", daemon=True
                )
                self._thread.start()
        future: Future[T] = Future()
        self._jobs.put((operation, future))
        deadline = time.monotonic() + timeout
        while True:
            try:
                return future.result(timeout=POLL_SECONDS)
            except FutureTimeout:
                if self._cancel.is_set():
                    raise BrowserUnavailable("The run was cancelled.") from None
                if time.monotonic() > deadline:
                    raise BrowserUnavailable(
                        f"The browser did not finish within {timeout:g}s; it may be busy or "
                        "stuck. Try Browser Close and open the page again."
                    ) from None

    # -- on the worker --------------------------------------------------------------------------

    def _session(self, agent: str, stage: str | None) -> BrowserSession:
        with self._lock:
            existing = self._sessions.get(agent)
        if existing is not None:
            return existing[0]
        if len(self._sessions) >= self._max_contexts:
            raise BrowserLimitReached(
                f"{self._max_contexts} browser contexts are open "
                f"({', '.join(sorted(self._sessions))}); the limit is browser.max_contexts. "
                "Wait for a teammate to finish, or Browser Close one of yours."
            )
        if self._driver is None:
            self._driver = self._driver_factory()  # raises BrowserUnavailable with the fix
        session = self._driver.new_session(agent)
        with self._lock:
            self._sessions[agent] = (session, stage)
        self._events.emit("browser.session", action="opened", agent=agent)
        return session

    def _close_session(self, agent: str, reason: str) -> bool:
        with self._lock:
            entry = self._sessions.pop(agent, None)
        if entry is None:
            return False
        try:
            entry[0].close()
        finally:
            self._events.emit("browser.session", action="closed", agent=agent, reason=reason)
        return True

    def _close_where(self, match: Callable[[str | None], bool], reason: str) -> None:
        with self._lock:
            agents = [agent for agent, (_, stage) in self._sessions.items() if match(stage)]
        for agent in agents:
            with contextlib.suppress(Exception):  # one stuck page must not keep the others open
                self._close_session(agent, reason)

    def _shutdown(self, reason: str) -> None:
        self._close_where(lambda stage: True, reason)
        driver, self._driver = self._driver, None
        if driver is not None:
            with contextlib.suppress(Exception):
                driver.close()
