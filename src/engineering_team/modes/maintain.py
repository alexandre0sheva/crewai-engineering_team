"""The controller actions behind the ``maintain`` recipes (no model is called here).

* ``coverage_before`` / ``coverage_after``: line coverage from the project's own coverage tool,
  so ``add-tests`` can report a delta measured by the controller rather than claimed by an agent.
* ``refactor_precondition``: a behaviour-preserving change needs tests that pass first.
* ``dependency_audit`` / ``security_report``: the ecosystem's audit tool (pip-audit, npm audit,
  cargo-audit) run by the controller, merged with the security engineer's findings.
"""

from __future__ import annotations

from engineering_team.contracts import Finding, Severity
from engineering_team.devtools.detect import find_stacks
from engineering_team.devtools.models import AuditReport, FileCoverage, Vulnerability
from engineering_team.devtools.runner import DevRunner
from engineering_team.modes.findings_report import gate, write_findings
from engineering_team.modes.maintain_contracts import AuditNote, CoverageDelta, CoverageSnapshot
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.tools.support import ToolError
from engineering_team.verification.profiles import project_roots

MAX_LEAST_COVERED = 15
MAX_BRIEF_VULNERABILITIES = 15
NO_COVERAGE = (
    "No coverage tool was detected (coverage.py or pytest-cov, jest or vitest, go test -cover, "
    "cargo-llvm-cov), so no coverage delta can be reported. Install one to get it."
)
SEVERITY_OF: dict[str, Severity] = {
    "critical": "critical", "high": "high", "moderate": "medium", "medium": "medium",
    "low": "low", "info": "info",
}  # fmt: skip


# -- coverage ----------------------------------------------------------------------------------


def measure_coverage(ctx: RunContext) -> CoverageSnapshot:
    """Run the tests under the project's coverage tool (each project root) and add it up."""

    runner = DevRunner(ctx)
    stacks = [s for s in project_roots(find_stacks(ctx.workspace.root)) if s.test and s.coverage]
    if not stacks:
        return CoverageSnapshot(note=NO_COVERAGE)
    covered = total = 0
    files: list[FileCoverage] = []
    tools: list[str] = []
    notes: list[str] = []
    for stack in stacks:
        try:
            report = runner.coverage(stack.directory)
        except ToolError as exc:
            notes.append(f"{stack.directory}: {exc}")
            continue
        if report.status in ("passed", "failed"):
            covered += report.covered
            total += report.total
            files += report.files
            tools.append(report.tool)
        else:
            notes.append(f"{stack.directory}: {report.hint or 'coverage could not be measured'}")
    if not tools:
        return CoverageSnapshot(note="; ".join(notes)[:500])
    lowest = sorted((f for f in files if f.total), key=lambda f: (f.percent, f.path))
    return CoverageSnapshot(
        status="measured",
        tool=", ".join(dict.fromkeys(tools)),
        covered=covered,
        total=total,
        least_covered=[
            f.model_copy(update={"uncovered_ranges": []}) for f in lowest[:MAX_LEAST_COVERED]
        ],
        note="; ".join(notes)[:500],
    )


def _summary(snapshot: CoverageSnapshot) -> str:
    if snapshot.status != "measured":
        return f"Coverage unavailable: {snapshot.note}"[:300]
    return f"{snapshot.percent}% of {snapshot.total} line(s) covered ({snapshot.tool})."


@register_action("coverage_before")
def coverage_before(ctx: RunContext, state: PipelineState) -> str:
    snapshot = measure_coverage(ctx)
    state.coverage = CoverageDelta(before=snapshot)
    ctx.events.emit("coverage.before", **_event(snapshot))
    return _summary(snapshot)


@register_action("coverage_after")
def coverage_after(ctx: RunContext, state: PipelineState) -> str:
    snapshot = measure_coverage(ctx)
    delta = state.coverage or CoverageDelta()
    state.coverage = delta.model_copy(update={"after": snapshot})
    ctx.events.emit("coverage.after", **_event(snapshot), change=state.coverage.change)
    change = state.coverage.change
    gained = f" ({change:+} points)" if change is not None else ""
    return _summary(snapshot) + gained


def _event(snapshot: CoverageSnapshot) -> dict[str, object]:
    return {"status": snapshot.status, "percent": snapshot.percent, "tool": snapshot.tool}


def coverage_brief(state: PipelineState) -> str:
    """Where the tests are thinnest, for the agent that writes them (empty when unmeasured)."""

    before = state.coverage.before if state.coverage else None
    if before is None or before.status != "measured":
        return ""
    rows = [f"- {f.path}: {f.percent}% ({f.covered}/{f.total} lines)" for f in before.least_covered]
    return f"Coverage before your work: {before.percent}%. Least covered files:\n" + "\n".join(rows)


# -- refactor ----------------------------------------------------------------------------------


@register_action("refactor_precondition")
def refactor_precondition(ctx: RunContext, state: PipelineState) -> str:
    """A refactor must not change behaviour, and only passing tests can say so: stop, asking for
    tests, when the baseline has none that pass or any that already fail."""

    checks = [c for c in (state.baseline.checks if state.baseline else []) if c.kind == "test"]
    passing = [c for c in checks if c.status == "passed"]
    failing = [c for c in checks if c.status == "failed"]
    if passing and not failing:
        return f"The project's tests pass before the change ({', '.join(c.id for c in passing)})."
    if failing:
        why = "its own tests already fail: " + "; ".join(f"{c.id} ({c.summary})" for c in failing)
        questions = [
            "Fix the failing tests first (`engineering-team fix`), then run the refactor again.",
            "Or give the refactor as a `--task custom` goal if a behaviour change is acceptable.",
        ]
    else:
        why = "no test of the project could be run (none found, or the tool is not installed)"
        questions = [
            "Run `engineering-team maintain --task add-tests` first, so the refactor has tests "
            "that pin today's behaviour.",
            "If tests exist, install what they need so the baseline can run them.",
        ]
    state.needs_info = questions
    raise NeedsInfo(
        f"A behaviour-preserving refactor needs passing tests before it starts, and {why}. "
        "Nothing was changed.",
        questions,
    )


# -- security audit ----------------------------------------------------------------------------


def _finding(vulnerability: Vulnerability, directory: str, number: int) -> Finding:
    where = f" in {directory}" if directory != "." else ""
    title = f": {vulnerability.title}" if vulnerability.title else ""
    fixed = (
        f"Upgrade {vulnerability.package} to {vulnerability.fixed_in} or later."
        if vulnerability.fixed_in
        else f"No fixed version is known for {vulnerability.package}; look for an alternative."
    )
    return Finding(
        id=f"D-{number}",
        severity=SEVERITY_OF.get(vulnerability.severity.lower(), "medium"),
        summary=(
            f"{vulnerability.package} {vulnerability.version}{where} has a known "
            f"vulnerability {vulnerability.id}{title}"
        ).strip()[:400],
        suggested_fix=fixed,
        source_role="dependency_audit",
    )


@register_action("dependency_audit")
def dependency_audit(ctx: RunContext, state: PipelineState) -> str:
    """Run the audit tool of each project (it needs the network: ``tools.dev.allow_network``)."""

    runner = DevRunner(ctx)
    state.audit, state.audit_findings = [], []
    stacks = [s for s in project_roots(find_stacks(ctx.workspace.root)) if s.audit]
    if not stacks:
        state.audit.append(AuditNote(note="No dependency audit tool applies to this project."))
    for stack in stacks:
        try:
            report: AuditReport = runner.audit(stack.directory)
        except ToolError as exc:
            state.audit.append(
                AuditNote(directory=stack.directory, tool=stack.audit or "", note=str(exc)[:300])
            )
            continue
        found = report.vulnerabilities
        for item in found:
            state.audit_findings.append(
                _finding(item, stack.directory, len(state.audit_findings) + 1)
            )
        state.audit.append(
            AuditNote(
                directory=stack.directory,
                tool=report.tool,
                status=report.status,
                count=len(found) + report.omitted,
                note="" if report.status in ("passed", "failed") else (report.hint or "")[:300],
            )
        )
    ran = [n for n in state.audit if n.status in ("passed", "failed")]
    ctx.events.emit("audit.finished", notes=len(state.audit), findings=len(state.audit_findings))
    if not ran:
        return (
            "No dependency audit could run: "
            + "; ".join(n.note for n in state.audit if n.note)[:250]
        )
    return f"{len(state.audit_findings)} known vulnerabilit(ies) in {len(ran)} project(s)."


def audit_brief(state: PipelineState) -> str:
    """What the controller's audit found, as text for the reviewers' prompt."""

    if not state.audit:
        return ""
    lines = ["Dependency audit run by the controller (a fact, from the ecosystem's own tool):"]
    for note in state.audit:
        what = f"{note.tool or 'no tool'} {note.status}"
        lines.append(f"- {note.directory}: {what}" + (f" ({note.note})" if note.note else ""))
    lines += [
        f"  {f.id} [{f.severity}] {f.summary}"
        for f in state.audit_findings[:MAX_BRIEF_VULNERABILITIES]
    ]
    return "\n".join(lines)


@register_action("security_report")
def security_report(ctx: RunContext, state: PipelineState) -> str:
    """Merge the dependency audit into the findings and write ``findings.json`` and
    ``findings.md`` to the run directory."""

    state.findings = [*state.findings, *state.audit_findings]
    path = write_findings(ctx, state, kind="security-audit", title="Security audit findings")
    return f"{len(state.findings)} finding(s) written to {path.name} and findings.md."


@register_action("security_gate")
def security_gate(ctx: RunContext, state: PipelineState) -> str:
    """An audit that was not asked to fix anything fails (exit 3) on a serious finding."""

    return gate(ctx, state, kind="security-audit", title="Security audit findings")
