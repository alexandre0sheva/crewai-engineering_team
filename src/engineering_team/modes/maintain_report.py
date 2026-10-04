"""The ``maintain`` sections of ``CHANGE_SUMMARY.md``: coverage and dependency upgrades.

Every number and every outcome is the controller's own measurement (the coverage tool's report, the
checks that said an upgrade could not go in); only the reasons an analyst gave for an upgrade or
for leaving a dependency alone are its words, and they are labelled.
"""

from __future__ import annotations

import re

from engineering_team.modes.maintain_contracts import CoverageSnapshot
from engineering_team.pipeline.state import PipelineState


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def _coverage_line(label: str, snapshot: CoverageSnapshot | None) -> str:
    if snapshot is None:
        return f"- {label}: not measured"
    if snapshot.status != "measured":
        return f"- {label}: unavailable. {snapshot.note}".rstrip()
    return (
        f"- {label}: {snapshot.percent}% "
        f"({snapshot.covered} of {snapshot.total} lines, {snapshot.tool})"
    )


def render_coverage(state: PipelineState) -> list[str]:
    delta = state.coverage
    if delta is None:
        return []
    lines = ["## Coverage", "", _coverage_line("Before", delta.before)]
    lines.append(_coverage_line("After", delta.after))
    if delta.change is not None:
        lines.append(f"- Change: {delta.change:+} percentage points (measured by the controller)")
    after = delta.after if delta.after and delta.after.status == "measured" else None
    if after and after.least_covered:
        lines += ["", "Still least covered:", ""]
        lines += [f"- `{f.path}`: {f.percent}%" for f in after.least_covered[:8]]
    return lines


def render_dependencies(state: PipelineState) -> list[str]:
    outcomes, plan = state.upgrade_outcomes, state.upgrades
    if not outcomes and not (plan and plan.skipped):
        return []
    done = [o for o in outcomes if o.status == "upgraded"]
    failed = [o for o in outcomes if o.status == "failed"]
    lines = ["## Dependencies", "", f"{len(done)} upgraded, {len(failed)} could not be upgraded."]
    if done:
        lines += ["", "### Upgraded", "", "| Package | From | To | Why (the analyst's words) |"]
        lines.append("|---|---|---|---|")
        lines += [
            f"| {_cell(o.upgrade.package)} | {_cell(o.upgrade.current)} "
            f"| {_cell(o.upgrade.target)} | {_cell(o.upgrade.reason)} |"
            for o in done
        ]
    if failed:
        lines += ["", "### Could not be upgraded (and why, from the controller's checks)", ""]
        lines += ["| Package | Tried | What the checks said |", "|---|---|---|"]
        lines += [
            f"| {_cell(o.upgrade.package)} | {_cell(o.upgrade.current)} -> "
            f"{_cell(o.upgrade.target)} | {_cell(o.reason)} |"
            for o in failed
        ]
    if plan and plan.skipped:
        lines += ["", "### Left alone on purpose (the analyst's reasons)", ""]
        lines += [f"- {_cell(item)}" for item in plan.skipped]
    return lines


def render_maintain_sections(state: PipelineState) -> list[str]:
    """Lines for the summary (each section starts with a heading); empty for other modes."""

    sections = [render_coverage(state), render_dependencies(state)]
    return [line for section in sections if section for line in ["", *section]]
