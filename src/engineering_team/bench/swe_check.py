"""The hidden checks of an imported SWE-bench instance (standard library and bench helpers only).

``bench import-swe`` writes each task an ``acceptance/checks.py`` of two lines that bind its
criteria to :func:`group_check`, beside ``swe.json`` (the instance's commit, tests and image) and
``test.patch`` (the tests the instance's fix is judged by, never shown to the team).

``fail-to-pass`` holds when every test the fix should make pass passes, ``pass-to-pass`` when every
test that passed before still does. Both run :mod:`engineering_team.bench.swe_runner` in the
instance's environment: its Docker image by default (the SWE-bench image, no network), or on this
machine with ``--acceptance-sandbox local`` (the repository from the cache, this Python), which
only suits repositories whose tests run with this machine's packages, such as a synthetic one.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from engineering_team.bench.checklib import SANDBOX_ENV, CheckError, expect, run
from engineering_team.bench.repos import CACHE_ENV, checkout_dir

RUNNER = Path(__file__).resolve().parent / "swe_runner.py"
RESULT_MARKER = "BENCH-SWE-RESULT "  # swe_runner.RESULT_MARKER (the runner imports nothing)
PATCH_FAILED = 4  # swe_runner.PATCH_FAILED
PASSING = ("PASSED", "XFAIL")  # what the SWE-bench harness counts as a pass
SPEC_FILE = "swe.json"
TEST_PATCH = "test.patch"
DEFAULT_TIMEOUT = 900.0
SHOWN = 10  # failing tests named in a failure message
LOG_SHOWN = 400  # characters of pytest's output when a test was not reported at all
MOUNT = "/bench"


def load_spec(acceptance: Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads((acceptance / SPEC_FILE).read_text(encoding="utf-8"))
    return spec


def docker_command(
    spec: dict[str, Any], workspace: Path, acceptance: Path, group: str
) -> list[str]:
    """The ``docker run`` that runs one group of tests in the instance's image (no network)."""

    command = [
        "docker", "run", "--rm", "--network", "none", "--memory", "4g", "--pids-limit", "1024",
        "-v", f"{workspace.resolve()}:{MOUNT}/src:ro",
        "-v", f"{acceptance.resolve()}:{MOUNT}/acceptance:ro",
        "-v", f"{RUNNER}:{MOUNT}/swe_runner.py:ro",
    ]  # fmt: skip
    if spec.get("platform"):
        command += ["--platform", str(spec["platform"])]
    return command + [
        str(spec["image"]), str(spec["python"]), f"{MOUNT}/swe_runner.py",
        "--testbed", str(spec.get("testbed", "/testbed")), "--src", f"{MOUNT}/src",
        "--spec", f"{MOUNT}/acceptance/{SPEC_FILE}", "--patch", f"{MOUNT}/acceptance/{TEST_PATCH}",
        "--group", group,
    ]  # fmt: skip


def _docker_ready(image: str, workspace: Path) -> None:
    if shutil.which("docker") is None:
        raise CheckError("docker is not on PATH: install Docker to run SWE-bench checks")
    probe = run(["docker", "image", "inspect", image], workspace, timeout=60)
    if probe.returncode != 0:
        if "daemon" in probe.stderr.lower() or "connect" in probe.stderr.lower():
            raise CheckError("Docker is not running: start it to run SWE-bench checks")
        raise CheckError(f"no image {image}: fetch it with `docker pull {image}`")


def local_command(spec: dict[str, Any], workspace: Path, acceptance: Path, group: str) -> list[str]:
    """The runner on this machine, over a copy of the cached checkout next to ``workspace``."""

    cache = os.environ.get(CACHE_ENV)
    base = checkout_dir(Path(cache), spec["url"], spec["commit"]) if cache else None
    if base is None or not (base / ".git").is_dir():
        raise CheckError(
            f"the repository is not in the cache ({CACHE_ENV}); a run of the task fetches it"
        )
    testbed = workspace.parent / "testbed"
    shutil.copytree(base, testbed, symlinks=True)
    return [
        sys.executable, str(RUNNER), "--testbed", str(testbed), "--src", str(workspace),
        "--spec", str(acceptance / SPEC_FILE), "--patch", str(acceptance / TEST_PATCH),
        "--group", group,
    ]  # fmt: skip


def run_group(workspace: Path, acceptance: Path, group: str) -> tuple[dict[str, str], str]:
    """Run one group's tests on the team's ``workspace``: each test's status, and the end of
    pytest's output."""

    spec = load_spec(acceptance)
    timeout = float(spec.get("timeout_seconds", DEFAULT_TIMEOUT))
    if os.environ.get(SANDBOX_ENV, "docker") == "local":
        command = local_command(spec, workspace, acceptance, group)
    else:
        _docker_ready(str(spec["image"]), workspace)
        command = docker_command(spec, workspace, acceptance, group)
    ran = run(command, workspace, timeout=timeout)
    result = _result(ran.stdout)
    if ran.returncode == PATCH_FAILED:  # the team's tree no longer takes the tests: its doing
        raise AssertionError(str(result.get("error") if result else "the test patch failed"))
    if ran.returncode != 0 or result is None:
        output = (ran.stdout + ran.stderr).strip()[-600:]
        raise CheckError(f"the test runner failed (exit {ran.returncode}): {output}")
    statuses: dict[str, str] = result["statuses"]
    return statuses, str(result.get("log", ""))


def _result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_MARKER):
            result: dict[str, Any] = json.loads(line[len(RESULT_MARKER) :])
            return result
    return None


def group_check(checks_file: str, group: str) -> Callable[[Path], None]:
    """``check_<criterion>`` for a task's ``checks.py``: every test of ``group`` must pass."""

    acceptance = Path(checks_file).resolve().parent

    def check(workspace: Path) -> None:
        statuses, log = run_group(workspace, acceptance, group)
        failing = {test: status for test, status in statuses.items() if status not in PASSING}
        shown = ", ".join(f"{test} ({status})" for test, status in list(failing.items())[:SHOWN])
        more = f" and {len(failing) - SHOWN} more" if len(failing) > SHOWN else ""
        why = f"\npytest said: {log[-LOG_SHOWN:]}" if "MISSING" in failing.values() else ""
        expect(
            not failing, f"{len(failing)} of {len(statuses)} tests do not pass: {shown}{more}{why}"
        )

    return check
