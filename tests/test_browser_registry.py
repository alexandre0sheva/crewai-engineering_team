"""The per-run browser registry: lazy start, one worker thread, limits, and cleanup (no browser)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from engineering_team.runtime.browsers import (
    BrowserLimitReached,
    BrowserRegistry,
    BrowserUnavailable,
)
from engineering_team.runtime.events import NullSink, stage_scope


class FakeSession:
    def __init__(self, agent: str, thread: int) -> None:
        self.agent, self.created_on, self.closed = agent, thread, False

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.closed = False
        self.created_on = threading.get_ident()

    def new_session(self, agent: str) -> FakeSession:
        session = FakeSession(agent, threading.get_ident())
        self.sessions.append(session)
        return session

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, max_contexts: int = 2) -> None:
        self.drivers: list[FakeDriver] = []
        self.cancel = threading.Event()
        self.registry = BrowserRegistry(
            max_contexts=max_contexts,
            driver_factory=self._driver,
            cancel_event=self.cancel,
            events=NullSink(),
        )

    def _driver(self) -> FakeDriver:
        driver = FakeDriver()
        self.drivers.append(driver)
        return driver


@pytest.fixture
def harness() -> Iterator[Harness]:
    harness = Harness()
    yield harness
    harness.registry.close_all("test over")  # a failing test must not leak a worker thread


def test_nothing_starts_until_the_first_use(harness: Harness) -> None:
    assert harness.drivers == [] and harness.registry.open_agents() == []

    harness.registry.close_all("never used")

    assert harness.drivers == []  # closing an unused registry starts nothing


def test_every_operation_runs_on_one_dedicated_thread(harness: Harness) -> None:
    caller = threading.get_ident()
    threads = set()

    def work(session: FakeSession) -> int:
        threads.add(threading.get_ident())
        return session.created_on

    first = harness.registry.run("a", work)
    results: list[int] = []
    other = threading.Thread(target=lambda: results.append(harness.registry.run("b", work)))
    other.start()
    other.join()

    assert len(threads) == 1 and caller not in threads
    assert first == results[0] == next(iter(threads))
    assert len(harness.drivers) == 1  # one browser for the run


def test_an_agent_keeps_its_session_and_agents_do_not_share(harness: Harness) -> None:
    a1 = harness.registry.run("frontend", lambda s: s)
    a2 = harness.registry.run("frontend", lambda s: s)
    b = harness.registry.run("qa", lambda s: s)

    assert a1 is a2 and a1 is not b
    assert sorted(harness.registry.open_agents()) == ["frontend", "qa"]


def test_the_context_limit_is_enforced_and_closing_frees_a_slot(harness: Harness) -> None:
    harness.registry.run("a", lambda s: s)
    harness.registry.run("b", lambda s: s)

    with pytest.raises(
        BrowserLimitReached, match="2 browser contexts are open.*browser.max_contexts"
    ):
        harness.registry.run("c", lambda s: s)
    assert harness.registry.close_agent("a") is True
    harness.registry.run("c", lambda s: s)

    assert sorted(harness.registry.open_agents()) == ["b", "c"]
    assert harness.registry.close_agent("nobody") is False


def test_closing_a_stage_closes_only_its_sessions(harness: Harness) -> None:
    with stage_scope("build"):
        built = harness.registry.run("a", lambda s: s)
    with stage_scope("verify"):
        verifying = harness.registry.run("b", lambda s: s)

    harness.registry.stop_stage("build")

    assert built.closed and not verifying.closed
    assert harness.registry.open_agents() == ["b"]


def test_closing_the_run_closes_everything_and_the_worker_and_is_idempotent(
    harness: Harness,
) -> None:
    session = harness.registry.run("a", lambda s: s)
    assert harness.registry.worker_alive

    harness.registry.close_all("the run ended")
    harness.registry.close_all("again")

    assert session.closed and harness.drivers[0].closed
    assert harness.registry.open_agents() == []
    assert not harness.registry.worker_alive
    with pytest.raises(BrowserUnavailable, match="closed"):
        harness.registry.run("a", lambda s: s)


def test_operation_errors_reach_the_caller_and_the_registry_keeps_working(
    harness: Harness,
) -> None:
    def fail(session: FakeSession) -> None:
        raise ValueError("page exploded")

    with pytest.raises(ValueError, match="page exploded"):
        harness.registry.run("a", fail)

    assert harness.registry.run("a", lambda s: "ok") == "ok"


def test_a_cancelled_run_stops_waiting_for_a_slow_operation(harness: Harness) -> None:
    release = threading.Event()
    threading.Timer(0.3, harness.cancel.set).start()

    with pytest.raises(BrowserUnavailable, match="cancelled"):
        harness.registry.run("a", lambda s: release.wait(5))
    release.set()
    harness.registry.close_all("test over")


def test_a_slow_operation_times_out_for_the_caller(harness: Harness) -> None:
    release = threading.Event()

    with pytest.raises(BrowserUnavailable, match="did not finish within 0.3"):
        harness.registry.run("a", lambda s: release.wait(5), timeout=0.3)
    release.set()
    harness.registry.close_all("test over")


def test_a_driver_that_cannot_start_reports_why_and_can_be_retried() -> None:
    attempts: list[int] = []

    def factory() -> FakeDriver:
        attempts.append(1)
        if len(attempts) == 1:
            raise BrowserUnavailable("Chromium is not installed")
        return FakeDriver()

    registry = BrowserRegistry(
        max_contexts=1, driver_factory=factory, cancel_event=threading.Event(), events=NullSink()
    )

    with pytest.raises(BrowserUnavailable, match="not installed"):
        registry.run("a", lambda s: s)
    assert registry.run("a", lambda s: "ready") == "ready"
    registry.close_all("done")


def test_the_worker_does_not_outlive_a_closed_registry_even_after_errors(harness: Harness) -> None:
    harness.registry.run("a", lambda s: s)
    time.sleep(0.05)

    harness.registry.close_all("test over")

    assert not harness.registry.worker_alive
