"""Recording the baseline: what the project's own checks say *before* the team changes anything.

An existing project is rarely green. The controller runs the detected checks (tests, lint, type
check, build: the same ones the verifier would pick, through the same ``Verifier`` and the same
execution backend) once, up front, and records which of them already fail and which individual
tests or diagnostics are the known pre-existing failures (``BaselineReport``). A later
verification can then say "no new failures" instead of "failures", which is the only fair
question to ask of a change to code that was already broken.
"""

from __future__ import annotations

from engineering_team.contracts import CheckResult
from engineering_team.modes.baseline_report import (
    BaselineCheck,
    BaselineReport,
    failure_keys,
    save_baseline,
)
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.runtime.context import RunContext
from engineering_team.verification.profiles import build_checks
from engineering_team.verification.revision import verification_revision
from engineering_team.verification.verifier import Verifier

BATCH_LABEL = "baseline"
MAX_SUMMARY = 300


def run_baseline(ctx: RunContext, profile: RepoProfile) -> BaselineReport:
    """Run the detected checks of the workspace and record the outcome (also written to
    ``.engineering-team/baseline.json``). Nothing is installed and no check is required: a
    failure is data here, not an error."""

    notes: list[str] = []
    testable = {c.directory for c in profile.commands_of("test")}
    planned = build_checks(ctx, None, []).checks
    checks = []
    for check in planned:
        if check.kind == "test" and check.cwd not in testable:
            notes.append(f"No test command was detected in {check.cwd}, so no tests were run.")
            continue
        checks.append(check)
    if not checks:
        notes.append("No checks could be detected, so there is no baseline to record.")
    results = Verifier(ctx).run(checks, label=BATCH_LABEL) if checks else []
    by_id = {check.id: check for check in checks}
    rows = [_row(result, by_id[result.id].cwd) for result in results]
    report = BaselineReport(
        revision=verification_revision(ctx.workspace),
        checks=rows,
        known_failures=sorted({key for row in rows for key in row.failing}),
        notes=notes,
    )
    for row in rows:
        if row.status == "unavailable":
            report.notes.append(f"{row.name} could not run: {row.hint or row.summary}")
        elif row.summary == "no tests found":
            report.notes.append(
                f"No tests were found in {row.directory}, so there is no test baseline."
            )
    save_baseline(ctx.workspace.root, report)
    ctx.events.emit(
        "baseline.recorded",
        checks={row.id: row.status for row in rows},
        known_failures=len(report.known_failures),
    )
    return report


def _found_no_tests(result: CheckResult) -> bool:
    """A test command that ran and found nothing to run: there is no test baseline, which is
    not the same as a failing one."""

    hint = (result.report or {}).get("hint") or ""
    return result.kind == "test" and result.status == "failed" and hint.startswith("No tests ran")


def _row(result: CheckResult, directory: str) -> BaselineCheck:
    none = _found_no_tests(result)
    return BaselineCheck(
        id=result.id,
        name=result.name,
        kind=result.kind,
        directory=directory,
        status="skipped" if none else result.status,
        summary="no tests found" if none else result.summary[:MAX_SUMMARY],
        command=result.command,
        exit_code=result.exit_code,
        duration=result.duration,
        log_path=result.log_path,
        hint=result.hint,
        failing=[] if none or result.status != "failed" else failure_keys(result, directory),
    )
