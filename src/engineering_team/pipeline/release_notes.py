"""The release notes of a new project, written by the controller (no model).

``docs/release-report.md`` used to be a release-readiness session of its own. Everything it
reported is already in the run's records (the spec, the plan, the verification, the review), so
the controller writes it from them: it costs nothing, and it cannot claim more than was shown.
"""

from __future__ import annotations

from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.verification.revision import RELEASE_FILE

MAX_ITEMS = 20


def _line(text: str) -> str:
    return " ".join(text.split())


def render_release_notes(state: PipelineState) -> str:
    spec, plan, record = state.spec, state.plan, state.verification
    title = spec.title.strip() if spec and spec.title.strip() else "Release notes"
    lines = [f"# {title}: release notes", ""]
    lines += [
        "Written by the controller from the run's records (the specification, the plan, its own "
        "check runs and the review); no agent's claim is repeated here as a fact.",
        "",
    ]
    if spec and spec.summary.strip():
        lines += [_line(spec.summary), ""]
    lines += ["## Verification", "", f"Verdict: {record.verdict or 'not verified'}.", ""]
    coverage = {item.id: item.status for item in record.coverage}
    if spec and spec.criteria:
        lines += ["## Acceptance criteria", ""]
        lines += [
            f"- **{c.id}** ({coverage.get(c.id, 'unverified')}): {_line(c.text)}"
            for c in spec.criteria
        ]
        lines.append("")
    if state.checks:
        lines += ["## Checks the controller ran", ""]
        lines += [
            f"- {check.name or check.id}: {check.status}"
            + (f" ({_line(check.summary)})" if check.summary else "")
            for check in state.checks[:MAX_ITEMS]
        ]
        lines.append("")
    if plan is not None:
        commands = plan.commands.model_dump(exclude={"schema_version"})
        listed = [f"- {name}: `{'`, `'.join(argv)}`" for name, argv in commands.items() if argv]
        lines += ["## How to run it", "", f"Stack: {_line(plan.stack) or 'not stated'}.", ""]
        lines += [*(listed or ["- See README.md."]), ""]
    open_items = [*record.problems, *(f"{f.severity}: {_line(f.summary)}" for f in state.findings)]
    limits = [*open_items, *(spec.non_goals if spec else []), *(plan.risks if plan else [])]
    if limits:
        lines += ["## Known limitations, open findings and risks", ""]
        lines += [f"- {_line(item)}" for item in limits[:MAX_ITEMS]]
        lines.append("")
    lines += [
        "This is MVP readiness as far as the checks above show it, not production readiness.",
        "",
    ]
    return "\n".join(lines)


@register_action("release_notes")
def release_notes_action(ctx: RunContext, state: PipelineState) -> str:
    """Write ``docs/release-report.md`` from the run's records."""

    ctx.workspace.write_file(RELEASE_FILE, render_release_notes(state))
    return f"{RELEASE_FILE} written by the controller from the run's records."
