"""Visual Studio test results (``dotnet test --logger trx``)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from engineering_team.devtools.models import TestFailure, TestReport
from engineering_team.devtools.parsers.common import excerpt, first_line, rel_path

TRX_FRAME = re.compile(r" in (?P<file>.+?):line (?P<line>\d+)")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _seconds(duration: str | None) -> float:
    try:
        hours, minutes, seconds = (duration or "").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return 0.0


def parse_trx(xml: str, *, root: str | None = None) -> TestReport:
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML ({exc})") from exc
    passed = failed = skipped = 0
    duration = 0.0
    failures: list[TestFailure] = []
    for result in (el for el in tree.iter() if _local(el.tag) == "UnitTestResult"):
        duration += _seconds(result.get("duration"))
        outcome = result.get("outcome")
        if outcome == "Passed":
            passed += 1
        elif outcome == "Failed":
            failed += 1
            message = trace = ""
            for node in result.iter():
                if _local(node.tag) == "Message":
                    message = node.text or ""
                elif _local(node.tag) == "StackTrace":
                    trace = node.text or ""
            frame = TRX_FRAME.search(trace)
            failures.append(
                TestFailure(
                    test_id=result.get("testName") or "",
                    file=rel_path(frame["file"], root) if frame else None,
                    line=int(frame["line"]) if frame else None,
                    message=excerpt(first_line(message), 300),
                    trace_excerpt=excerpt(f"{message.strip()}\n{trace.strip()}", 1200, lines=15),
                )
            )
        else:  # NotExecuted, Inconclusive, ...
            skipped += 1
    return TestReport(
        framework="dotnet",
        status="failed" if failed else "passed",
        passed=passed,
        failed=failed,
        skipped=skipped,
        duration=round(duration, 3),
        failures=failures,
    )
