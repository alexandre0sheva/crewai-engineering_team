"""The ``live`` and ``docker`` markers skip by default; the offline guard blocks real hosts."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from conftest import docker_is_available, skip_reason

pytest_plugins = ["pytester"]


def _no_docker() -> bool:
    return False


def _docker() -> bool:
    return True


def test_unmarked_tests_are_never_skipped() -> None:
    assert skip_reason([], {}, _no_docker) is None


def test_live_tests_need_the_opt_in_and_a_provider_key() -> None:
    assert "ENGINEERING_LIVE_TESTS" in str(skip_reason(["live"], {}, _docker))
    assert "API key" in str(skip_reason(["live"], {"ENGINEERING_LIVE_TESTS": "1"}, _docker))
    assert (
        skip_reason(["live"], {"ENGINEERING_LIVE_TESTS": "1", "OPENAI_API_KEY": "k"}, _docker)
        is None
    )
    assert skip_reason(["live"], {"OPENAI_API_KEY": "k"}, _docker) is not None


def test_docker_tests_are_skipped_without_a_daemon() -> None:
    assert "Docker" in str(skip_reason(["docker"], {}, _no_docker))
    assert skip_reason(["docker"], {}, _docker) is None


def test_the_daemon_is_not_probed_when_no_test_needs_it() -> None:
    def explode() -> bool:
        raise AssertionError("docker was probed")

    assert skip_reason(["live"], {}, explode) is not None
    assert skip_reason([], {}, explode) is None


def test_the_collection_hook_skips_marked_tests_by_default(pytester: pytest.Pytester) -> None:
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text(encoding="utf-8"))
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.live
        def test_live(): raise AssertionError("must not run")

        @pytest.mark.docker
        def test_docker(): pass

        def test_plain(): pass
        """
    )
    pytester.makeini("[pytest]\nmarkers =\n    live: x\n    docker: y\n")

    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs")

    docker_runs = docker_is_available()  # the docker test runs only where a daemon answers
    result.assert_outcomes(passed=1 + docker_runs, skipped=2 - docker_runs)
    assert "live test: set ENGINEERING_LIVE_TESTS=1" in result.stdout.str()


def test_a_test_that_opens_an_external_connection_fails_offline() -> None:
    with pytest.raises(RuntimeError, match="stay offline"):
        socket.create_connection(("203.0.113.10", 80), timeout=1)


def test_loopback_connections_are_still_allowed() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
