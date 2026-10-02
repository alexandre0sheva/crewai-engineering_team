"""Compact text for the reports: the verdict first, then only what an agent needs to act on."""

from __future__ import annotations

from engineering_team.devtools.models import (
    AuditReport,
    CoverageReport,
    DevReport,
    DiagnosticReport,
    InstallReport,
    TestReport,
)

TRACE_LINES = 8
DETAILED_FAILURES = 3


def _where(file: str | None, line: int | None, col: int | None = None) -> str:
    if not file:
        return ""
    return f"{file}:{line}" + (f":{col}" if col else "") if line else file


def _footer(report: DevReport) -> list[str]:
    exit_text = f" (exit {report.exit_code})" if report.exit_code is not None else ""
    log = f" · full log: {report.log_path}" if report.log_path else ""
    lines = [f"Command: {report.command}{exit_text}{log}"] if report.command else []
    if report.hint:
        lines.append(f"Hint: {report.hint}")
    return lines


def _verdict(label: str, status: str) -> str:
    return f"{label}: {status.upper()}"


def render_tests(report: TestReport) -> str:
    counts = f"{report.passed} passed, {report.failed} failed"
    if report.errors:
        counts += f", {report.errors} errors"
    counts += f", {report.skipped} skipped in {report.duration:g}s"
    head = _verdict(f"Tests ({report.framework or 'unknown'})", report.status)
    counted = report.status in ("passed", "failed") and report.framework != "unknown"
    lines = [f"{head} - {counts}" if counted else head]
    lines += _footer(report)
    if report.failures:
        lines += ["", f"Failures ({len(report.failures) + report.failures_omitted}):"]
        for number, failure in enumerate(report.failures, 1):
            where = _where(failure.file, failure.line)
            lines.append(f"{number}. {failure.test_id}" + (f"  [{where}]" if where else ""))
            lines += [f"   {row}" for row in failure.message.splitlines()[:3]]
            if number <= DETAILED_FAILURES and failure.trace_excerpt:
                trace = failure.trace_excerpt.splitlines()[:TRACE_LINES]
                lines += [f"   | {row}" for row in trace]
        if report.failures_omitted:
            lines.append(f"... {report.failures_omitted} more failing tests not shown.")
        lines += ["", "Iterate with Run Single Test (id above); Rerun Failed Tests after a fix."]
    if report.raw_tail and (report.status != "passed" or report.framework == "unknown"):
        lines += ["", "Output (tail):", report.raw_tail.strip()]
    return "\n".join(lines)


def render_diagnostics(report: DiagnosticReport) -> str:
    label = {"lint": "Lint", "typecheck": "Type check", "build": "Build", "format": "Format"}[
        report.kind
    ]
    head = _verdict(f"{label} ({report.tool})", report.status)
    if report.kind == "format":
        detail = f"{len(report.files)} file(s) need formatting" if report.files else ""
    else:
        detail = f"{report.errors} errors, {report.warnings} warnings" if report.diagnostics else ""
    lines = [f"{head} - {detail}" if detail else head]
    lines += _footer(report)
    if report.files:
        lines += ["", *report.files]
    if report.diagnostics:
        lines.append("")
        for item in report.diagnostics:
            where = _where(item.file, item.line, item.col)
            rule = f" {item.rule}" if item.rule else ""
            lines.append(f"{where} {item.severity}{rule}: {item.message}".strip())
        if report.omitted:
            lines.append(f"... {report.omitted} more not shown; narrow with paths.")
    if report.raw_tail:
        lines += ["", "Output (tail):", report.raw_tail.strip()]
    return "\n".join(lines)


def render_coverage(report: CoverageReport) -> str:
    head = _verdict(f"Coverage ({report.tool})", report.status)
    lines = [f"{head} - {report.percent:g}% ({report.covered}/{report.total} lines or statements)"]
    if report.status in ("unavailable", "error"):
        lines = [head]
    if report.tests is not None:
        tests = report.tests
        lines.append(
            f"Tests: {tests.status} ({tests.passed} passed, {tests.failed} failed, "
            f"{tests.errors} errors, {tests.skipped} skipped)"
        )
    lines += _footer(report)
    if report.detail is not None:
        detail = report.detail
        lines += ["", f"{detail.path}: {detail.percent:g}% ({detail.covered}/{detail.total})"]
        lines.append("Uncovered lines: " + (", ".join(detail.uncovered_ranges) or "none"))
    elif report.files:
        lines += ["", "Least covered files:"]
        lines += [f"- {f.path}: {f.percent:g}% ({f.covered}/{f.total})" for f in report.files]
        lines.append("Pass file=<path> to list that file's uncovered lines.")
    if report.tests is not None and report.tests.failures:
        lines += ["", "Failing tests:"]
        lines += [f"- {f.test_id}: {f.message}" for f in report.tests.failures[:10]]
    return "\n".join(lines)


def render_install(report: InstallReport) -> str:
    lines = [_verdict(f"Install ({report.manager})", report.status), *_footer(report)]
    if report.status != "passed" and report.raw_tail:
        lines += ["", "Output (tail):", report.raw_tail.strip()]
    return "\n".join(lines)


def render_audit(report: AuditReport) -> str:
    head = _verdict(f"Dependency audit ({report.tool})", report.status)
    total = len(report.vulnerabilities) + report.omitted
    lines = [f"{head} - {total} vulnerable package(s)" if report.status == "failed" else head]
    if report.status == "passed":
        lines[0] += " - no known vulnerabilities"
    lines += _footer(report)
    if report.vulnerabilities:
        lines.append("")
        for vuln in report.vulnerabilities:
            fix = f"; fixed in {vuln.fixed_in}" if vuln.fixed_in else ""
            lines.append(
                f"{vuln.package} {vuln.version} [{vuln.severity}] {vuln.id}: {vuln.title}{fix}"
            )
        if report.omitted:
            lines.append(f"... {report.omitted} more not shown.")
    if report.raw_tail:
        lines += ["", "Output (tail):", report.raw_tail.strip()]
    return "\n".join(lines)
