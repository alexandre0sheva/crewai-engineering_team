"""The Docker sandbox against a real daemon (skipped unless ``docker info`` works).

Each test is a canary: something the workload must not be able to see or do, tried for real.
They use ``alpine`` (tiny; pulled on first use) and run commands through the backend directly,
so they test the boundary, not the allowlist in front of it.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.execution.backend import CommandSpec
from engineering_team.execution.docker import DockerBackend
from engineering_team.runtime.processes import free_loopback_port
from engineering_team.settings import DockerSettings

pytestmark = pytest.mark.docker

IMAGE = "alpine:3.20"
SERVER_IMAGE = "python:3.12-alpine"  # alpine's busybox has no httpd applet
RUN_ID = "itest-canary"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    return root


@pytest.fixture
def sandbox(project: Path, tmp_path: Path) -> Callable[..., DockerBackend]:
    created: list[DockerBackend] = []

    def build(image: str = IMAGE, **options: object) -> DockerBackend:
        backend = DockerBackend(
            project,
            tmp_path / "logs",
            RUN_ID,
            DockerSettings(image=image),
            **options,  # type: ignore[arg-type]
        )
        created.append(backend)
        return backend

    yield build  # type: ignore[misc]
    for backend in created:
        backend.close()


def sh(project: Path, script: str, **changes: object) -> CommandSpec:
    values: dict[str, object] = {
        "argv": ("sh", "-c", script),
        "cwd": project,
        "env": {"HOME": str(project / ".engineering-team" / "tool-home")},
        "timeout": 120.0,
        "label": "canary",
    }
    return CommandSpec(**{**values, **changes})  # type: ignore[arg-type]


def leftovers() -> list[str]:
    listed = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", f"label=engineering-team.run={RUN_ID}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listed.stdout.split()


def test_a_file_outside_the_project_is_invisible(
    sandbox: Callable[..., DockerBackend], project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text("SECRET-CANARY", encoding="utf-8")

    record = sandbox().run(sh(project, f"cat {canary}; ls {outside}"))

    assert record.exit_code != 0
    assert "SECRET-CANARY" not in record.output_tail
    assert "No such file" in record.output_tail


def test_an_api_key_in_the_host_environment_is_invisible(
    sandbox: Callable[..., DockerBackend], project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-canary-must-not-leak")
    monkeypatch.setenv("ENGINEERING_CANARY_TOKEN", "token-canary-must-not-leak")

    record = sandbox().run(sh(project, "env; cat /proc/self/environ | tr '\\0' '\\n'"))

    assert record.exit_code == 0
    assert "canary-must-not-leak" not in record.output_tail
    assert "OPENAI" not in record.output_tail


def test_the_network_is_off_for_ordinary_commands(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    record = sandbox().run(
        sh(project, "cat /proc/net/dev; wget -q -T 3 -O /dev/null http://1.1.1.1/ && echo REACHED")
    )

    assert record.exit_code != 0
    assert "REACHED" not in record.output_tail
    assert "eth0" not in record.output_tail  # only the loopback device exists


def test_controller_state_is_hidden_but_scratch_is_shared(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    state = project / ".engineering-team"
    (state / "runs").mkdir(parents=True)
    (state / "runs" / "pinned-checks.yaml").write_text("secret: 1\n", encoding="utf-8")
    (state / "tmp").mkdir()

    record = sandbox().run(
        sh(project, f"ls {state}/runs; ls {state}; echo hi > {state}/tmp/from-container.txt")
    )

    assert "pinned-checks.yaml" not in record.output_tail
    assert (state / "tmp" / "from-container.txt").read_text(encoding="utf-8") == "hi\n"
    assert (state / "runs" / "pinned-checks.yaml").exists()  # hidden, not deleted


def test_the_workload_is_not_root_and_cannot_write_outside_the_project(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    record = sandbox().run(
        sh(
            project,
            "id -u; touch /rootfs-write 2>&1; echo ok > /tmp/scratch && echo TMP-OK; "
            "echo mine > project-file.txt && echo PROJECT-OK; "
            "grep CapEff /proc/self/status; grep NoNewPrivs /proc/self/status",
        )
    )

    lines = record.output_tail.splitlines()
    assert lines[0] == str(os.getuid())
    assert "Read-only file system" in record.output_tail
    assert "TMP-OK" in record.output_tail and "PROJECT-OK" in record.output_tail
    assert (project / "project-file.txt").stat().st_uid == os.getuid()
    assert "CapEff:\t0000000000000000" in record.output_tail
    assert "NoNewPrivs:\t1" in record.output_tail


def test_an_output_flood_is_bounded(sandbox: Callable[..., DockerBackend], project: Path) -> None:
    record = sandbox(max_log_bytes=1_000_000).run(
        sh(project, "yes 'a line of flood output' | head -c 30000000")
    )

    assert record.truncated
    assert len(record.output_tail) < 20_000
    assert record.log_path.stat().st_size < 1_100_000


def test_the_container_is_removed_after_a_timeout(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    started = time.monotonic()

    record = sandbox().run(sh(project, "sleep 120", timeout=3.0))

    assert record.timed_out and time.monotonic() - started < 60
    assert leftovers() == []


def test_the_container_is_removed_when_the_run_is_cancelled(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    cancel = threading.Event()
    threading.Timer(3.0, cancel.set).start()

    record = sandbox(cancel_event=cancel).run(sh(project, "sleep 120"))

    assert record.cancelled
    assert leftovers() == []


def test_a_server_is_reachable_on_loopback_and_gone_when_stopped(
    sandbox: Callable[..., DockerBackend], project: Path
) -> None:
    (project / "index.html").write_text("served from a container", encoding="utf-8")
    port = free_loopback_port()
    server = f"python -m http.server {port} --bind 0.0.0.0 --directory {project}"
    handle = sandbox(image=SERVER_IMAGE).start(sh(project, server, ports=(port,), timeout=300.0))
    try:
        body = ""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not body:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=2) as r:
                    body = r.read().decode()
            except OSError:
                time.sleep(0.3)
        assert body == "served from a container"
    finally:
        handle.stop()

    assert leftovers() == []
