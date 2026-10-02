"""Test frameworks that only print text: unittest, cargo test, minitest."""

from __future__ import annotations

import re

from engineering_team.devtools.models import TestFailure, TestReport
from engineering_team.devtools.parsers.common import excerpt, first_line, rel_path, strip_ansi

# -- unittest ----------------------------------------------------------------------------

UNITTEST_BLOCK = re.compile(r"^(?P<kind>ERROR|FAIL): (?P<name>\S+)(?: \((?P<cls>[^)]+)\))?\s*$")
UNITTEST_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')
UNITTEST_RAN = re.compile(r"^Ran (?P<n>\d+) tests? in (?P<secs>[\d.]+)s")
UNITTEST_RESULT = re.compile(r"^(?:OK|FAILED)(?: \((?P<detail>[^)]*)\))?\s*$")
RULE = re.compile(r"^(={20,}|-{20,})$")


def parse_unittest(text: str, *, root: str | None = None) -> TestReport:
    """``python -m unittest`` output (verbose or not): counts from the footer, failures from
    the ``FAIL:``/``ERROR:`` blocks, each located at the traceback frame inside the test."""

    lines = strip_ansi(text).splitlines()
    total, duration, counts = 0, 0.0, {"failures": 0, "errors": 0, "skipped": 0}
    for line in lines:
        if match := UNITTEST_RAN.match(line):
            total, duration = int(match["n"]), float(match["secs"])
        elif (match := UNITTEST_RESULT.match(line)) and match["detail"]:
            for part in match["detail"].split(","):
                name, _, number = part.strip().partition("=")
                if name in counts and number.isdigit():
                    counts[name] = int(number)
    failures: list[TestFailure] = []
    index = 0
    while index < len(lines):
        header = UNITTEST_BLOCK.match(lines[index])
        index += 1
        if not header:
            continue
        body: list[str] = []
        while index < len(lines) and not (
            RULE.match(lines[index]) and lines[index].startswith("=")
        ):
            if lines[index].startswith("Ran ") and UNITTEST_RAN.match(lines[index]):
                break
            body.append(lines[index])
            index += 1
        trace = "\n".join(line for line in body if not RULE.match(line)).strip("\n")
        name, cls = header["name"], header["cls"]
        test_id = f"{cls}.{name}" if cls and "_FailedTest" not in cls else name
        frames = [m for line in trace.splitlines() if (m := UNITTEST_FRAME.match(line))]
        frame = next((m for m in frames if m["func"] == name), frames[0] if frames else None)
        message_lines = [line for line in trace.splitlines() if line and not line.startswith(" ")]
        message = next(
            (line for line in reversed(message_lines) if not line.startswith("Traceback")), ""
        )
        failures.append(
            TestFailure(
                test_id=test_id,
                file=rel_path(frame["file"], root) if frame else None,
                line=int(frame["line"]) if frame else None,
                message=excerpt(message or first_line(trace), 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
    failed, errors, skipped = counts["failures"], counts["errors"], counts["skipped"]
    return TestReport(
        framework="unittest",
        status="failed" if failed or errors else "passed",
        passed=max(total - failed - errors - skipped, 0),
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration=duration,
        failures=failures,
    )


# -- cargo test --------------------------------------------------------------------------

CARGO_TEST = re.compile(r"^test (?P<name>\S+) \.\.\. (?P<outcome>ok|FAILED|ignored)")
CARGO_RESULT = re.compile(
    r"^test result: (?:ok|FAILED)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; "
    r"(?P<ignored>\d+) ignored;.*finished in (?P<secs>[\d.]+)s"
)
CARGO_STDOUT = re.compile(r"^---- (?P<name>\S+) stdout ----$")
PANIC_NEW = re.compile(r"panicked at (?P<file>[^\s:]+):(?P<line>\d+):\d+:?$")
PANIC_OLD = re.compile(r"panicked at '(?P<msg>.*)', (?P<file>[^\s:]+):(?P<line>\d+):\d+$")


def parse_cargo_test(text: str) -> TestReport:
    """``cargo test`` output, possibly several test binaries and doc-tests in a row."""

    lines = strip_ansi(text).splitlines()
    passed = failed = ignored = 0
    duration = 0.0
    seen_result = False
    for line in lines:
        if match := CARGO_RESULT.match(line):
            seen_result = True
            passed += int(match["passed"])
            failed += int(match["failed"])
            ignored += int(match["ignored"])
            duration += float(match["secs"])
    if not seen_result:  # an interrupted run: count the lines that did print
        for line in lines:
            if match := CARGO_TEST.match(line):
                outcome = match["outcome"]
                passed += outcome == "ok"
                failed += outcome == "FAILED"
                ignored += outcome == "ignored"
    failures: list[TestFailure] = []
    index = 0
    while index < len(lines):
        header = CARGO_STDOUT.match(lines[index])
        index += 1
        if not header:
            continue
        body: list[str] = []
        while (
            index < len(lines)
            and not CARGO_STDOUT.match(lines[index])
            and lines[index].strip() != "failures:"
        ):
            body.append(lines[index])
            index += 1
        trace = "\n".join(body).strip("\n")
        file, line_number, message = None, None, ""
        for position, row in enumerate(body):
            if old := PANIC_OLD.search(row):
                file, line_number, message = old["file"], int(old["line"]), old["msg"]
                break
            if new := PANIC_NEW.search(row):
                file, line_number = new["file"], int(new["line"])
                message = next((r.strip() for r in body[position + 1 :] if r.strip()), "")
                break
        failures.append(
            TestFailure(
                test_id=header["name"],
                file=file,
                line=line_number,
                message=excerpt(message or first_line(trace), 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
    return TestReport(
        framework="cargo",
        status="failed" if failed else "passed",
        passed=passed,
        failed=failed,
        skipped=ignored,
        duration=round(duration, 3),
        failures=failures,
    )


# -- minitest ----------------------------------------------------------------------------

MINITEST_BLOCK = re.compile(r"^\s*\d+\) (?P<kind>Failure|Error):\s*$")
MINITEST_ID = re.compile(r"^(?P<id>[\w:]+#\S+?)(?: \[(?P<file>[^\]:]+):(?P<line>\d+)\])?:?\s*$")
MINITEST_SUMMARY = re.compile(
    r"(?P<runs>\d+) runs?, \d+ assertions?, (?P<failures>\d+) failures?, "
    r"(?P<errors>\d+) errors?, (?P<skips>\d+) skips?"
)
MINITEST_TIME = re.compile(r"^Finished in (?P<secs>[\d.]+)s")
RUBY_FRAME = re.compile(r"^\s*(?P<file>[^\s:]+\.rb):(?P<line>\d+)")


def parse_minitest(text: str) -> TestReport:
    """Minitest's default reporter (also what ``rails test`` and ``rake test`` print)."""

    lines = strip_ansi(text).splitlines()
    runs = failed = errors = skipped = 0
    duration = 0.0
    for line in lines:
        if match := MINITEST_SUMMARY.search(line):
            runs, failed = int(match["runs"]), int(match["failures"])
            errors, skipped = int(match["errors"]), int(match["skips"])
        elif match := MINITEST_TIME.match(line):
            duration = float(match["secs"])
    failures: list[TestFailure] = []
    index = 0
    while index < len(lines):
        header = MINITEST_BLOCK.match(lines[index])
        index += 1
        if not header or index >= len(lines):
            continue
        ident = MINITEST_ID.match(lines[index])
        if not ident:
            continue
        index += 1
        body: list[str] = []
        while (
            index < len(lines)
            and not MINITEST_BLOCK.match(lines[index])
            and not MINITEST_SUMMARY.search(lines[index])
        ):
            body.append(lines[index])
            index += 1
        trace = "\n".join(body).strip("\n")
        file, line_number = ident["file"], int(ident["line"]) if ident["line"] else None
        if file is None:
            frame = next((m for row in body if (m := RUBY_FRAME.match(row))), None)
            if frame:
                file, line_number = frame["file"], int(frame["line"])
        failures.append(
            TestFailure(
                test_id=ident["id"],
                file=rel_path(file) if file else None,
                line=line_number,
                message=excerpt(first_line(trace), 300),
                trace_excerpt=excerpt(trace, 1200, lines=15),
            )
        )
    return TestReport(
        framework="minitest",
        status="failed" if failed or errors else "passed",
        passed=max(runs - failed - errors - skipped, 0),
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration=duration,
        failures=failures,
    )
