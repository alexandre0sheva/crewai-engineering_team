"""Test frameworks with a JSON report: jest and vitest, ``go test -json``, RSpec."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from engineering_team.devtools.models import TestFailure, TestReport
from engineering_team.devtools.parsers.common import excerpt, first_line, rel_path, strip_ansi


class ReportError(ValueError):
    """The report is not the JSON the framework writes."""


def _load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ReportError(f"not valid JSON ({exc})") from exc


# -- jest and vitest ---------------------------------------------------------------------

JS_FRAME = re.compile(
    r"\((?P<file>[^()\s]+):(?P<line>\d+):\d+\)|at (?P<file2>[^()\s]+):(?P<line2>\d+):\d+"
)


def parse_jest(text: str, *, root: str | None = None, framework: str = "jest") -> TestReport:
    """Jest's ``--json`` report; vitest's ``--reporter=json`` has the same shape."""

    data = _load(text)
    if not isinstance(data, dict) or "testResults" not in data:
        raise ReportError("no 'testResults' key; was it written by jest --json or vitest?")
    passed = failed = skipped = errors = 0
    duration = 0.0
    failures: list[TestFailure] = []
    for suite in data["testResults"]:
        path = rel_path(str(suite.get("name", "")), root)
        results = suite.get("assertionResults") or []
        if not results and suite.get("status") == "failed":  # the file did not even load
            errors += 1
            message = strip_ansi(str(suite.get("message") or "the test file failed to run"))
            failures.append(
                TestFailure(
                    test_id=path,
                    file=path,
                    message=excerpt(first_line(message), 300),
                    trace_excerpt=excerpt(message, 1200, lines=15),
                )
            )
            continue
        for case in results:
            duration += float(case.get("duration") or 0) / 1000
            status = case.get("status")
            if status == "passed":
                passed += 1
            elif status == "failed":
                failed += 1
                trace = strip_ansi("\n".join(case.get("failureMessages") or []))
                located = next(
                    (m for m in JS_FRAME.finditer(trace) if "node_modules" not in m.group(0)),
                    None,
                )
                line = None
                if located:
                    line = int(located["line"] or located["line2"])
                failures.append(
                    TestFailure(
                        test_id=f"{path}::{case.get('fullName') or case.get('title')}",
                        file=path,
                        line=line,
                        message=excerpt(first_line(trace), 300),
                        trace_excerpt=excerpt(trace, 1200, lines=15),
                    )
                )
            else:  # pending, skipped, todo, disabled
                skipped += 1
    return TestReport(
        framework=framework,
        status="failed" if failed or errors else "passed",
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration=round(duration, 3),
        failures=failures,
    )


# -- go test -json -----------------------------------------------------------------------

GO_LOCATION = re.compile(r"^\s+(?P<file>[^\s:]+\.go):(?P<line>\d+):\s?(?P<msg>.*)$")
GO_NOISE = re.compile(r"^(=== (RUN|PAUSE|CONT|NAME)|--- (PASS|FAIL|SKIP)|PASS$|FAIL$|FAIL\s|ok\s)")


def parse_go_test(text: str, *, module: str | None = None) -> TestReport:
    """``go test -json`` events. Only leaf tests count (a parent fails because its subtest did);
    a package that failed without any failing test (it did not build) is one error."""

    outputs: dict[tuple[str, str], list[str]] = defaultdict(list)
    outcome: dict[tuple[str, str], str] = {}
    elapsed: dict[tuple[str, str], float] = {}
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        key = (str(event.get("Package", "")), str(event.get("Test", "")))
        action = event.get("Action")
        if action == "output":
            outputs[key].append(str(event.get("Output", "")))
        elif action in ("pass", "fail", "skip"):
            outcome[key] = action
            elapsed[key] = float(event.get("Elapsed") or 0)
    if not outcome:
        raise ReportError("no go test events; was it run with -json?")
    tests = {key for key in outcome if key[1]}
    parents = {(pkg, name.rsplit("/", 1)[0]) for pkg, name in tests if "/" in name}
    passed = failed = skipped = errors = 0
    failures: list[TestFailure] = []
    for key in sorted(tests - parents):
        result = outcome[key]
        if result == "pass":
            passed += 1
            continue
        if result == "skip":
            skipped += 1
            continue
        failed += 1
        package, name = key
        directory = package.removeprefix(module).strip("/") if module else ""
        file = line = None
        messages: list[str] = []
        for chunk in outputs[key]:
            for row in chunk.splitlines():
                if located := GO_LOCATION.match(row):
                    file = file or "/".join(filter(None, [directory, located["file"]]))
                    line = line or int(located["line"])
                    messages.append(located["msg"])
                elif row.strip() and not GO_NOISE.match(row):
                    messages.append(row.strip())
        trace = "".join(outputs[key]).strip("\n")
        failures.append(
            TestFailure(
                test_id=f"{package}::{name}",
                file=file,
                line=line,
                message=excerpt(messages[0] if messages else "failed", 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
    for (package, name), result in sorted(outcome.items()):
        if name or result != "fail":
            continue
        if any(pkg == package and outcome[(pkg, n)] == "fail" for pkg, n in tests):
            continue  # its failing tests already explain it
        errors += 1
        trace = "".join(outputs[(package, "")]).strip("\n")
        failures.append(
            TestFailure(
                test_id=package,
                message=excerpt(first_line(trace) or "package failed", 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
    duration = sum(value for (_, name), value in elapsed.items() if not name)
    return TestReport(
        framework="go",
        status="failed" if failed or errors else "passed",
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration=round(duration, 3),
        failures=failures,
    )


# -- RSpec -------------------------------------------------------------------------------


def parse_rspec(text: str) -> TestReport:
    """RSpec's ``--format json`` report."""

    data = _load(text)
    if not isinstance(data, dict) or "examples" not in data:
        raise ReportError("no 'examples' key; was it written by rspec --format json?")
    passed = failed = skipped = 0
    failures: list[TestFailure] = []
    for example in data["examples"]:
        status = example.get("status")
        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
            error = example.get("exception") or {}
            backtrace = "\n".join(error.get("backtrace") or [])
            message = str(error.get("message") or "")
            failures.append(
                TestFailure(
                    test_id=str(example.get("id") or example.get("full_description")),
                    file=rel_path(str(example.get("file_path") or "")) or None,
                    line=example.get("line_number"),
                    message=excerpt(first_line(f"{error.get('class', '')}: {message}"), 300),
                    trace_excerpt=excerpt(f"{message}\n{backtrace}", 1200, lines=15),
                )
            )
        else:
            skipped += 1
    summary = data.get("summary") or {}
    errors = int(summary.get("errors_outside_of_examples_count") or 0)
    return TestReport(
        framework="rspec",
        status="failed" if failed or errors else "passed",
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration=round(float(summary.get("duration") or 0), 3),
        failures=failures,
    )
