"""The shapes every developer tool returns, whatever framework or linter produced them.

Parsers turn a tool's own output into these; the runners add where the log is and how the
command went. They are :class:`~engineering_team.contracts.Contract` models so the verifier
(T18) can store and compare them.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from engineering_team.contracts import Contract

# ``passed``/``failed``: the tool ran and said so. ``error``: it ran but produced no usable
# result (a crash, a collection error, a missing report). ``unavailable``: it could not run (not
# installed, no project) - never to be read as a pass. ``unknown``: an unrecognised tool's raw log.
DevStatus = Literal["passed", "failed", "error", "unavailable", "unknown"]
DiagnosticSeverity = Literal["error", "warning", "info"]
DiagnosticKind = Literal["lint", "typecheck", "build", "format"]


class DevReport(Contract):
    """What every dev tool result carries."""

    status: DevStatus = "unknown"
    command: str = ""
    exit_code: int | None = None
    duration: float = 0.0
    log_path: str | None = None
    hint: str | None = None  # what to do next, e.g. how to install a missing tool


# -- tests -------------------------------------------------------------------------------


class TestFailure(Contract):
    __test__: ClassVar[bool] = False  # not a pytest test class, despite the name

    test_id: str
    file: str | None = None
    line: int | None = None
    message: str = ""
    trace_excerpt: str = ""


class TestReport(DevReport):
    __test__: ClassVar[bool] = False

    framework: str = ""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    failures: list[TestFailure] = Field(default_factory=list)
    failures_omitted: int = 0  # failures beyond the cap the tool reports
    raw_tail: str = ""  # an unknown framework's output, or why a run produced no report

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors


# -- lint, type check, build, format ----------------------------------------------------


class Diagnostic(Contract):
    file: str | None = None
    line: int | None = None
    col: int | None = None
    rule: str | None = None
    severity: DiagnosticSeverity = "error"
    message: str = ""


class DiagnosticReport(DevReport):
    kind: DiagnosticKind = "lint"
    tool: str = ""
    errors: int = 0
    warnings: int = 0
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    omitted: int = 0  # diagnostics beyond the cap
    files: list[str] = Field(default_factory=list)  # format: files that need (or got) formatting
    raw_tail: str = ""


# -- coverage ----------------------------------------------------------------------------


class FileCoverage(Contract):
    path: str
    covered: int = 0
    total: int = 0
    uncovered_ranges: list[str] = Field(default_factory=list)  # "12-15", "20"

    @property
    def percent(self) -> float:
        return round(100.0 * self.covered / self.total, 1) if self.total else 100.0


class CoverageReport(DevReport):
    tool: str = ""
    covered: int = 0
    total: int = 0
    files: list[FileCoverage] = Field(default_factory=list)
    detail: FileCoverage | None = None  # the file asked about, with its uncovered ranges
    tests: TestReport | None = None  # the run that produced the coverage

    @property
    def percent(self) -> float:
        return round(100.0 * self.covered / self.total, 1) if self.total else 0.0


# -- dependencies ------------------------------------------------------------------------


class InstallReport(DevReport):
    manager: str = ""
    raw_tail: str = ""


class Vulnerability(Contract):
    package: str
    version: str = ""
    id: str = ""
    severity: str = "unknown"
    fixed_in: str = ""
    title: str = ""


class AuditReport(DevReport):
    tool: str = ""
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    omitted: int = 0
    raw_tail: str = ""
