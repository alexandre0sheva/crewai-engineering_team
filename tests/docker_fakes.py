"""A fake ``docker`` executable, so the Docker backend's lifecycle is tested without Docker.

The fake records every invocation as a JSON line in ``$DOCKER_FAKE_LOG`` (``DOCKER_*`` variables
are the ones the backend lets through to the client) and acts out ``DOCKER_FAKE_MODE`` for
``docker run``: ``echo`` (print and exit), ``exit3``, ``flood`` (a lot of output), ``hang``
(until ``docker stop`` or ``docker rm`` kills it, like a real container).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

SCRIPT = r"""#!{python}
import json, os, signal, sys, time

args = sys.argv[1:]
log = os.environ["DOCKER_FAKE_LOG"]
mode = os.environ.get("DOCKER_FAKE_MODE", "echo")
record = {{"args": args}}
if args[:1] == ["run"] and "--env-file" in args:
    env_path = args[args.index("--env-file") + 1]
    with open(env_path, encoding="utf-8") as handle:
        record["env_file"] = handle.read()
    record["env_mode"] = oct(os.stat(env_path).st_mode & 0o777)
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps(record) + "\n")

pid_file = log + ".pid"
if args[:1] == ["version"]:
    if os.environ.get("DOCKER_FAKE_DAEMON") == "down":
        print("Cannot connect to the Docker daemon at unix:///x. Is the docker daemon running?",
              file=sys.stderr)
        sys.exit(1)
    print(os.environ.get("DOCKER_FAKE_VERSION", "27.3.1 linux"))
elif args[:2] == ["image", "inspect"]:
    sys.exit(0 if os.environ.get("DOCKER_FAKE_IMAGES", "present") == "present" else 1)
elif args[:1] == ["pull"]:
    if os.environ.get("DOCKER_FAKE_PULL") == "fail":
        print("Error response from daemon: pull access denied for nope", file=sys.stderr)
        sys.exit(1)
elif args[:1] == ["ps"]:
    print("c0ffee\nbeef01")
elif args[:1] in (["stop"], ["rm"]):
    if os.path.exists(pid_file):
        with open(pid_file) as handle:
            pid = int(handle.read())
        try:
            os.kill(pid, signal.SIGTERM if args[0] == "stop" else signal.SIGKILL)
        except ProcessLookupError:
            pass
elif args[:1] == ["run"]:
    with open(pid_file, "w") as handle:
        handle.write(str(os.getpid()))
    if mode == "echo":
        print("hello from the container")
    elif mode == "exit3":
        print("failing")
        sys.exit(3)
    elif mode == "flood":
        for _ in range(2000):
            print("x" * 1000)
    elif mode == "hang":
        print("started", flush=True)
        time.sleep(60)
"""


def install_fake_docker(directory: Path) -> Path:
    """Write the fake ``docker`` into ``directory`` and return its path."""

    path = directory / "docker"
    path.write_text(SCRIPT.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def calls(log: Path) -> list[dict[str, object]]:
    """Every recorded invocation, in order."""

    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def verbs(log: Path) -> list[str]:
    """The first argument of each invocation (``run``, ``stop``, ``rm``, ...)."""

    return [str(call["args"][0]) for call in calls(log)]  # type: ignore[index]


def fake_environment(log: Path, **extra: str) -> dict[str, str]:
    return {**os.environ, "DOCKER_FAKE_LOG": str(log), **extra}
