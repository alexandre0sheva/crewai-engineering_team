"""Helpers for a task's hidden ``acceptance/checks.py`` (standard library only).

A checks file defines ``check_<criterion_id>(workspace)`` functions (dashes in the id become
underscores) that raise ``AssertionError`` (use :func:`expect`) when the behaviour is wrong, and
ends with ``if __name__ == "__main__": main()``. The harness runs one criterion per process::

    python checks.py <criterion-id> <workspace-copy>

It judges only behaviour: it runs the team's program and looks at what it does.
"""

from __future__ import annotations

import contextlib
import http.client
import json
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
    workspace: Path, argv: Sequence[str], port: int, *, wait: float = 15.0
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
        print(f"no check_{criterion.replace('-', '_')} in the checks file")
        sys.exit(2)
    try:
        function(workspace)
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    except Exception:  # a check that crashes is a failed criterion with a traceback, never a pass
        print("FAILED: the check itself raised\n" + traceback.format_exc(limit=4))
        sys.exit(1)
    print("passed")
