"""JUnit XML: pytest, Maven/Gradle surefire, PHPUnit, and anything else that writes it."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Literal

from engineering_team.devtools.models import TestFailure, TestReport
from engineering_team.devtools.parsers.common import excerpt, first_line, rel_path

Flavor = Literal["pytest", "generic"]
LOCATION = re.compile(r"^(?P<file>[^\s:'\"]+\.[A-Za-z]+):(?P<line>\d+)(?::|\s|$)", re.MULTILINE)


class JUnitError(ValueError):
    """The report is not JUnit XML."""


def _int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _pytest_id(classname: str, name: str) -> tuple[str, str | None]:
    """``tests.test_calc.TestX`` + ``test_a`` -> (``tests/test_calc.py::TestX::test_a``, file)."""

    parts = [part for part in classname.split(".") if part]
    split = next((i for i, part in enumerate(parts) if part[:1].isupper()), len(parts))
    module, classes = parts[:split], parts[split:]
    if not module:  # a collection error: the "name" is the dotted module
        path = name.replace(".", "/") + ".py" if name and "." in name else None
        return (path or name), path
    path = "/".join(module) + ".py"
    return "::".join([path, *classes, name]), path


def _last_error_line(trace: str) -> str:
    """The last ``E   ...`` line of a pytest traceback: the exception itself."""

    lines = [line[1:].strip() for line in trace.splitlines() if line.startswith("E ")]
    return lines[-1] if lines else ""


def _locate(trace: str, path: str | None) -> tuple[str | None, int | None]:
    """Where the test failed: the traceback line in the test's own file, else the first one."""

    matches = [(m["file"], int(m["line"])) for m in LOCATION.finditer(trace)]
    for file, line in matches:
        if path and rel_path(file).endswith(path):
            return path, line
    return (rel_path(matches[0][0]), matches[0][1]) if matches else (path, None)


def parse_junit(xml: str, *, framework: str, flavor: Flavor = "generic") -> TestReport:
    """Counts and failures from a JUnit XML report (one ``testsuite`` or a ``testsuites`` root)."""

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise JUnitError(f"not valid XML ({exc})") from exc
    cases = list(root.iter("testcase"))
    if not cases and root.tag not in ("testsuite", "testsuites"):
        raise JUnitError(f"unexpected root element <{root.tag}>")
    passed = failed = skipped = errors = 0
    duration = 0.0
    failures: list[TestFailure] = []
    for case in cases:
        duration += float(case.get("time") or 0)
        classname, name = case.get("classname") or case.get("class") or "", case.get("name") or ""
        bad = case.find("failure")
        broken = case.find("error")
        problem = bad if bad is not None else broken
        if problem is None:
            if case.find("skipped") is not None:
                skipped += 1
            else:
                passed += 1
            continue
        if bad is not None:
            failed += 1
        else:
            errors += 1
        trace = problem.text or ""
        message = problem.get("message") or first_line(trace)
        if flavor == "pytest" and message == "collection failure":
            message = _last_error_line(trace) or message  # the cause is at the end
        if flavor == "pytest":
            test_id, path = _pytest_id(classname, name)
            file, line = _locate(trace, path)
        else:
            test_id = f"{classname}#{name}" if classname else name
            file, line = case.get("file"), _int(case.get("line")) or None
            if file is None:
                file, line = _locate(trace, None)
        failures.append(
            TestFailure(
                test_id=test_id,
                file=rel_path(file) if file else None,
                line=line,
                message=excerpt(message, 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
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
