"""Helpers for the web UI tests: a stand-in CLI that behaves like a run, and an API client.

The UI starts runs as subprocesses of ``python -m engineering_team <args>``. A test replaces that
command with ``tests/stub_run.py``, which takes the same arguments and goes through the same run
directory machinery (manifest, events, board, workspace lock, cancel flag, inbox), but without a
model: it works in small steps, so a test can cancel it, steer it, or answer its question.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cli_helpers import workspace_root
from fastapi.testclient import TestClient
from pipeline_fakes import settings_for

from engineering_team.ui.app import create_app
from engineering_team.ui.launcher import RunLauncher
from engineering_team.ui.security import Security


def write_stub(directory: Path | None = None) -> Path:
    """The stand-in CLI (``tests/stub_run.py``)."""

    return Path(__file__).parent / "stub_run.py"


def make_client(
    stub: Path | None = None,
    *,
    security: Security | None = None,
    max_concurrent: int = 2,
    headers: dict[str, str] | None = None,
    **overrides: object,
) -> tuple[TestClient, RunLauncher]:
    """An API client (as a page on localhost would be) over the sandbox workspace root."""

    root = workspace_root()
    settings = settings_for(root, **overrides)
    stub = stub or write_stub()
    launcher = RunLauncher(
        str(root), max_concurrent=max_concurrent, command=[sys.executable, str(stub)]
    )
    app = create_app(settings, security=security, launcher=launcher)
    client = TestClient(app, base_url="http://localhost")
    client.headers.update({"X-Engineering-Team": "1", **(headers or {})})
    return client, launcher


@contextlib.contextmanager
def serve(client: TestClient) -> Iterator[httpx.Client]:
    """The client's app on a real localhost socket (``TestClient`` buffers a streamed response
    until it ends, so live streaming needs a server); yields an ``httpx`` client for it."""

    import uvicorn

    config = uvicorn.Config(
        client.app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )  # type: ignore[arg-type]
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, "the server to start")
    port = server.servers[0].sockets[0].getsockname()[1]
    headers = {"X-Engineering-Team": "1"}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=30) as live:
            yield live
    finally:
        server.should_exit = True
        thread.join(10)


def wait_for(check: Callable[[], Any], what: str, timeout: float = 30.0) -> Any:
    """Poll until ``check()`` is truthy; return it. Fails with ``what`` after ``timeout``."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {what}")


API = "/api/v1"


def start(client: TestClient, request: str = "Build it.", **fields: object) -> str:
    response = client.post(f"{API}/runs", json={"mode": "new", "request": request, **fields})
    assert response.status_code == 202, response.text
    return str(response.json()["run_id"])


def start_running(client: TestClient, request: str = "SLOW", **fields: object) -> str:
    """Start a run and wait until it is running (its manifest exists)."""

    run_id = start(client, request, **fields)
    wait_status(client, run_id, "running")
    return run_id


def status_of(client: TestClient, run_id: str) -> str:
    return str(client.get(f"{API}/runs/{run_id}").json()["status"])


def wait_status(client: TestClient, run_id: str, *wanted: str, timeout: float = 30.0) -> str:
    return str(
        wait_for(
            lambda: (s := status_of(client, run_id)) in wanted and s,
            f"run {run_id} to be {'/'.join(wanted)} (is {status_of(client, run_id)})",
            timeout,
        )
    )


def sse_events(text: str) -> list[dict[str, Any]]:
    """The ``(id, data)`` frames of an SSE body as dicts with ``id`` and ``data`` (JSON)."""

    import json

    frames: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            frames.append(
                {
                    "id": fields.get("id"),
                    "event": fields.get("event"),
                    "data": json.loads(fields["data"]),
                }
            )
    return frames
