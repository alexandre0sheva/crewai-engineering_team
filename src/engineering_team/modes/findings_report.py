"""``findings.json`` and ``findings.md``: a review's or an audit's findings, for pipelines too.

Written by the controller from the consolidated findings (never from an agent's text), into the
run directory. The JSON carries what a CI step needs: whether the run passed, the threshold
(``review.fail_on``), counts per severity, and every finding; the Markdown is the same for people.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json, atomic_write_text
from engineering_team.contracts import Finding
from engineering_team.pipeline.review import RANK, SEVERITY_ORDER, blocking
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext

FINDINGS_JSON = "findings.json"
FINDINGS_MD = "findings.md"
SCHEMA = 1


def _cell(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()


def ordered(findings: list[Finding]) -> list[Finding]:
    """Most severe first; ties keep the controller's numbering."""

    return sorted(findings, key=lambda f: (-RANK[f.severity], f.id))


def document(ctx: RunContext, state: PipelineState, *, kind: str) -> dict[str, Any]:
    fail_on = ctx.settings.review.fail_on
    findings = ordered(state.findings)
    serious = blocking(findings, fail_on)
    iso = state.isolation or {}
    return {
        "schema_version": SCHEMA,
        "kind": kind,
        "run_id": ctx.run_id,
        "base": iso.get("base_commit"),
        "base_branch": iso.get("base_branch"),
        "head": ctx.git.head() if ctx.git.is_repo() else None,
        "fail_on": fail_on,
        "passed": not serious,
        "counts": {
            s: sum(1 for f in findings if f.severity == s) for s in reversed(SEVERITY_ORDER)
        },
        "findings": [f.model_dump(mode="json", exclude={"schema_version"}) for f in findings],
        "dependency_audit": [n.model_dump(mode="json") for n in state.audit],
    }


def render_markdown(data: dict[str, Any], title: str) -> str:
    verdict = "PASSED" if data["passed"] else "FAILED"
    serious = sum(c for s, c in data["counts"].items() if RANK[s] >= RANK[data["fail_on"]])
    lines = [
        f"# {title}",
        "",
        f"Run `{data['run_id']}`. Written by the controller from the reviewers' findings and its "
        "own audits; a finding is a claim to check, not a proven defect.",
        "",
        f"**Result: {verdict}.** {serious} finding(s) at {data['fail_on']} or above "
        f"(`review.fail_on`); {len(data['findings'])} in all.",
    ]
    if data.get("base"):
        label = data.get("base_branch") or "the base"
        lines += ["", f"Compared with {label} (`{str(data['base'])[:12]}`)."]
    lines += ["", "## Findings", ""]
    if data["findings"]:
        lines += ["| ID | Severity | Where | Finding | Suggested fix |", "|---|---|---|---|---|"]
        for f in data["findings"]:
            where = f["file"] or "(project-wide)"
            where += f":{f['line']}" if f.get("line") else ""
            lines.append(
                f"| {_cell(f['id'])} | {f['severity']} | `{_cell(where)}` | {_cell(f['summary'])} "
                f"| {_cell(f.get('suggested_fix'))} |"
            )
    else:
        lines.append("None.")
    if data["dependency_audit"]:
        lines += ["", "## Dependency audit", ""]
        for note in data["dependency_audit"]:
            what = f"{note['tool'] or 'no audit tool'}: {note['status']}"
            more = f" ({note['count']} known vulnerabilities)" if note["count"] else ""
            extra = f". {note['note']}" if note["note"] else ""
            lines.append(f"- `{note['directory']}` {what}{more}{extra}")
    return "\n".join(lines) + "\n"


def write_findings(ctx: RunContext, state: PipelineState, *, kind: str, title: str) -> Path:
    """Write both files to the run directory; returns the JSON's path."""

    data = document(ctx, state, kind=kind)
    atomic_write_json(ctx.run_dir / FINDINGS_JSON, data)
    atomic_write_text(ctx.run_dir / FINDINGS_MD, render_markdown(data, title))
    ctx.events.emit(
        "findings.written", kind=kind, passed=data["passed"], total=len(data["findings"])
    )
    return ctx.run_dir / FINDINGS_JSON


def read_findings(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def gate(ctx: RunContext, state: PipelineState, *, kind: str, title: str) -> str:
    """Write the findings, then end the run ``failed`` (verdict ``failed``, exit code 3) when one
    reaches ``review.fail_on``; ``verified`` otherwise."""

    from engineering_team.verification.loop import VerificationError

    path = write_findings(ctx, state, kind=kind, title=title)
    fail_on = ctx.settings.review.fail_on
    serious = blocking(state.findings, fail_on)
    if serious:
        state.verification.verdict = "failed"
        state.verification.problems = [f"{f.id} [{f.severity}] {f.summary}" for f in serious[:10]]
        raise VerificationError(
            f"{len(serious)} finding(s) at {fail_on} or above "
            f"({', '.join(f.id for f in serious[:8])}). See {path.name} and findings.md."
        )
    state.verification.verdict = "verified"
    return f"{len(state.findings)} finding(s), none at {fail_on} or above."
