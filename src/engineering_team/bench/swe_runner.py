"""Run an imported SWE-bench instance's tests on the team's work, in the instance's environment.

The hidden check (:mod:`engineering_team.bench.swe_check`) starts this file with the Python of the
instance's environment: in its Docker image (``/testbed`` holds the repository at the base commit,
installed), or on this machine over a copy of the cached checkout. It runs as a script, so it uses
the standard library only and stays valid on Python 3.6 (the oldest SWE-bench environments).

    python swe_runner.py --testbed DIR --src DIR --spec swe.json --patch test.patch --group G

1. The team's tree (``--src``) is laid over the testbed: its files are copied in, and files the
   repository tracks that the team removed are deleted.
2. The files the test patch touches are put back as they were at the base commit (the team may have
   edited them), and the test patch is applied, as the SWE-bench harness does.
3. ``pytest -rA`` runs the test files the group's ids (``fail_to_pass`` or ``pass_to_pass``) name,
   as the SWE-bench harness does, and the status of each id is read from the short summary and
   printed as one JSON line after ``RESULT_MARKER``. Files, not ids: SWE-bench's log parser cut ids
   at the first space (``test_x[a b]`` is stored as ``test_x[a``), and pytest stops the whole run
   at an id it cannot find.

Exit status 0: the tests ran (whatever their result); 4: the test patch did not apply to the
team's tree; anything else: the runner itself failed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional  # noqa: F401 (used by the type comments)

RESULT_MARKER = "BENCH-SWE-RESULT "
PATCH_FAILED = 4
LOG_LIMIT = 1500
# Never copied over the testbed (and never deleted from it): caches and environments.
SKIP_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".git", "node_modules",
    ".engineering-team",
}  # fmt: skip
STATUSES = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")
PASSING = ("PASSED", "XFAIL")  # what the SWE-bench harness counts as a pass


def git(testbed, *args):
    # type: (str, str) -> subprocess.CompletedProcess
    return subprocess.run(
        ["git", *args],
        cwd=testbed,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )


def overlay(src, testbed):
    # type: (str, str) -> None
    """Make the testbed's files the team's: copy every file in, delete tracked files it removed."""

    present = set()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES]
        for name in files:
            relative = os.path.relpath(os.path.join(root, name), src)
            present.add(relative.replace(os.sep, "/"))
            target = os.path.join(testbed, relative)
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            os.makedirs(os.path.dirname(target) or testbed, exist_ok=True)
            shutil.copy2(os.path.join(root, name), target)
    tracked = git(testbed, "ls-files", "-z").stdout.split("\0")
    for name in tracked:
        if name and name not in present and not SKIP_NAMES.intersection(name.split("/")):
            path = os.path.join(testbed, name)
            if os.path.lexists(path):
                os.remove(path)


def restore_and_patch(testbed, commit, test_files, patch):
    # type: (str, str, List[str], str) -> str
    """Put the test patch's files back as at ``commit`` and apply it; returns an error or ''."""

    for name in test_files:
        if git(testbed, "cat-file", "-e", "{}:{}".format(commit, name)).returncode == 0:
            git(testbed, "checkout", commit, "--", name)
        elif os.path.lexists(os.path.join(testbed, name)):
            os.remove(os.path.join(testbed, name))
    applied = git(testbed, "apply", "--whitespace=nowarn", patch)
    return "" if applied.returncode == 0 else applied.stdout.strip()[-LOG_LIMIT:]


def parse_statuses(output, test_ids):
    # type: (str, List[str]) -> Dict[str, str]
    """The status of each wanted test from ``pytest -rA`` output; ``MISSING`` when not reported.

    A summary line is ``STATUS <node id>`` and may go on with `` - message``. A wanted id matches
    the whole node id or the part before a space (the ids SWE-bench cut short), so an id holding
    `` - `` is still found. When several tests match one cut id, a failing one wins."""

    statuses = dict.fromkeys(test_ids, "MISSING")
    wanted = sorted(test_ids, key=len, reverse=True)
    for line in output.splitlines():
        for status in STATUSES:
            if not line.startswith(status + " "):
                continue
            rest = line[len(status) + 1 :]
            for test_id in wanted:
                if rest == test_id or rest.startswith(test_id + " "):
                    if statuses[test_id] in PASSING + ("MISSING",):
                        statuses[test_id] = status
                    break
            break
    return statuses


def files_to_run(test_ids):
    # type: (List[str]) -> List[str]
    """The files the ids name, in order (``tests/test_a.py::test_b`` -> ``tests/test_a.py``)."""

    files = []  # type: List[str]
    for test_id in test_ids:
        name = test_id.split("::", 1)[0]
        if name not in files:
            files.append(name)
    return files


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    parser = argparse.ArgumentParser(prog="swe_runner.py")
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--src", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--group", choices=("fail_to_pass", "pass_to_pass"), required=True)
    args = parser.parse_args(argv)
    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)
    overlay(args.src, args.testbed)
    error = restore_and_patch(args.testbed, spec["commit"], spec["test_files"], args.patch)
    if error:
        print(RESULT_MARKER + json.dumps({"error": "the test patch does not apply: " + error}))
        return PATCH_FAILED
    test_ids = spec[args.group]
    command = [sys.executable, "-m", "pytest", "-rA", "--tb=no", "-p", "no:cacheprovider"]
    ran = subprocess.run(
        command + files_to_run(test_ids),
        cwd=args.testbed,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    result = {
        "statuses": parse_statuses(ran.stdout, test_ids),
        "exit_code": ran.returncode,
        "log": ran.stdout[-LOG_LIMIT:],
    }
    print(RESULT_MARKER + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
