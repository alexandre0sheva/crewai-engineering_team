"""Helpers for a task's hidden ``acceptance/checks.py`` (standard library only).

A checks file defines ``check_<criterion_id>(workspace)`` functions (dashes in the id become
underscores) that raise ``AssertionError`` (use :func:`expect`) when the behaviour is wrong, and
ends with ``if __name__ == "__main__": main()``. The harness runs one criterion per process::

    python checks.py <criterion-id> <workspace-copy>

It judges only behaviour: it runs the team's program and looks at what it does. Exit status 0 is a
pass and 1 a fail; anything else (``CheckError``: a missing toolchain, Docker not running) means
the check could not judge, and the harness records an ``error``, never a fail.

Tasks that need Node or Go run those tools through :func:`toolchain`: in a Docker image by default
(``benchmarks/images/<name>/``, no network), or on this machine with ``--acceptance-sandbox local``.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 20.0
ERROR_EXIT = 3
SANDBOX_ENV = "BENCH_ACCEPTANCE_SANDBOX"  # docker (default) or local
NODE_MODULES_ENV = "BENCH_NODE_MODULES"  # local only: a node_modules directory with the task deps
IMAGES = {"node": "engineering-team-bench-node:1", "go": "engineering-team-bench-go:1"}
IMAGE_APP_DIR = "/opt/bench/app"  # the images keep their packages in /opt/bench/node_modules
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "0"}


class CheckError(Exception):
    """The check cannot judge (its toolchain is missing, Docker is down): an ``error``, not a fail.

    Never raise it for anything the team's work could cause."""


def expect(condition: object, message: str) -> None:
    """Fail the criterion with ``message`` unless ``condition`` holds."""

    if not condition:
        raise AssertionError(message)


def python(
    workspace: Path,
    *args: str,
    input: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``python *args`` in the workspace (stdout and stderr captured as text)."""

    return run([sys.executable, *args], workspace, input=input, timeout=timeout, env=env)


def run(
    argv: Sequence[str],
    cwd: Path,
    *,
    input: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell; a timeout fails the check instead of hanging it."""

    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(f"`{' '.join(argv)}` did not finish within {timeout:g}s") from None


def toolchain(
    workspace: Path,
    name: str,
    *argv: str,
    node_modules: bool = False,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` (``node ...``, ``npx vitest ...``, ``go test ...``) on the workspace with the
    ``name`` toolchain: in its Docker image, or locally with ``--acceptance-sandbox local``.

    ``node_modules``: the command needs the task's npm packages (the image has them; locally they
    come from ``--node-modules DIR``). The workspace is a scratch copy, so files may be added.
    """

    if name not in IMAGES:
        raise CheckError(f"unknown toolchain {name!r}")
    if os.environ.get(SANDBOX_ENV, "docker") == "local":
        return _local(workspace, name, list(argv), node_modules, timeout)
    return _docker(workspace, name, list(argv), timeout)


def _local(
    workspace: Path, name: str, argv: list[str], node_modules: bool, timeout: float
) -> subprocess.CompletedProcess[str]:
    program = argv[0]
    if "/" in program:  # a program in the workspace (a binary the check built)
        argv = [str((workspace / program).resolve()), *argv[1:]]
    elif shutil.which(program) is None:
        raise CheckError(
            f"{argv[0]} is not on PATH: install the {name} toolchain or use the Docker sandbox"
        )
    env = dict(os.environ)
    if name == "go":
        home = Path(env.get("HOME") or workspace.parent)
        env.update(GO_ENV, GOCACHE=str(home / "go-cache"), GOPATH=str(home / "go"))
    if node_modules and not (workspace / "node_modules").exists():
        modules = os.environ.get(NODE_MODULES_ENV)
        if not modules or not Path(modules).is_dir():
            raise CheckError(
                "this check needs the task's npm packages: pass --node-modules DIR (see "
                "benchmarks/images/node/package.json) or use the Docker sandbox"
            )
        (workspace / "node_modules").symlink_to(Path(modules).resolve(), target_is_directory=True)
    try:
        return run(argv, workspace, timeout=timeout, env=env)
    except OSError as exc:  # a binary that cannot run here (another CPU type, ...)
        raise CheckError(f"cannot run {argv[0]}: {exc}") from None


def _docker(
    workspace: Path, name: str, argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    image = IMAGES[name]
    if shutil.which("docker") is None:
        raise CheckError("docker is not on PATH: install Docker or pass --acceptance-sandbox local")
    probe = run(["docker", "image", "inspect", image], workspace, timeout=60)
    if probe.returncode != 0:
        if "daemon" in probe.stderr.lower() or "connect" in probe.stderr.lower():
            raise CheckError("Docker is not running: start it or pass --acceptance-sandbox local")
        raise CheckError(
            f"no image {image}: build it with `docker build -t {image} benchmarks/images/{name}`"
        )
    container = f"bench-check-{secrets.token_hex(4)}"
    settings = [f"{k}={v}" for k, v in GO_ENV.items()] if name == "go" else []
    command = [
        "docker", "run", "--rm", "--name", container, "--network", "none",
        "--memory", "2g", "--pids-limit", "512", "-e", "HOME=/tmp",
        *(arg for pair in settings for arg in ("-e", pair)),
        "-v", f"{workspace.resolve()}:{IMAGE_APP_DIR}", "-w", IMAGE_APP_DIR, image, *argv,
    ]  # fmt: skip
    if hasattr(os, "getuid"):
        command[3:3] = ["--user", f"{os.getuid()}:{os.getgid()}"]
    try:
        return run(command, workspace, timeout=timeout)
    finally:
        with contextlib.suppress(Exception):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            raise AssertionError(f"expected a JSON body, got: {self.text[:200]!r}") from None


def http_request(
    method: str,
    url: str,
    body: Any = None,
    *,
    form: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> Response:
    """One HTTP request that does not follow redirects (so a 302 can be checked)."""

    parts = urlsplit(url)
    headers: dict[str, str] = {}
    data: bytes | None = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        from urllib.parse import urlencode

        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    connection = http.client.HTTPConnection(
        parts.hostname or "127.0.0.1", parts.port, timeout=timeout
    )
    try:
        connection.request(
            method, parts.path + (f"?{parts.query}" if parts.query else ""), data, headers
        )
        reply = connection.getresponse()
        return Response(reply.status, {k.lower(): v for k, v in reply.getheaders()}, reply.read())
    except OSError as exc:
        raise AssertionError(f"{method} {url} failed: {exc}") from None
    finally:
        connection.close()


@contextlib.contextmanager
def serving(
    workspace: Path, argv: Sequence[str], port: int, *, wait: float = 30.0
) -> Iterator[str]:
    """Start ``python *argv`` (which must listen on ``port``), yield its base URL, stop it."""

    process = subprocess.Popen(
        [sys.executable, *argv],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + wait
        while True:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(
                    f"the server exited early ({process.returncode}): {output[-400:]}"
                )
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.3):
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"nothing listened on port {port} within {wait:g}s")
            time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()


def main() -> None:
    """Run the criterion named on the command line; exit 0 when it holds, 1 when it does not."""

    if len(sys.argv) != 3:
        sys.exit("usage: checks.py <criterion-id> <workspace>")
    criterion, workspace = sys.argv[1], Path(sys.argv[2]).resolve()
    function = vars(sys.modules["__main__"]).get(f"check_{criterion.replace('-', '_')}")
    if function is None:
        print(f"ERROR: no check_{criterion.replace('-', '_')} in the checks file")
        sys.exit(ERROR_EXIT)
    try:
        function(workspace)
    except CheckError as exc:
        print(f"ERROR: the check could not judge: {exc}")
        sys.exit(ERROR_EXIT)
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    except Exception:  # a check that crashes is a failed criterion with a traceback, never a pass
        print("FAILED: the check itself raised\n" + traceback.format_exc(limit=4))
        sys.exit(1)
    print("passed")
