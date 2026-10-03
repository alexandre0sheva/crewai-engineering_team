"""What a repository mode changed: the patch, the diff noise, and ``CHANGE_SUMMARY.md``.

All of it is measured by the controller from Git against the commit the team started from
(``state.isolation['base_commit']``), never taken from an agent's account. The files go to the
run directory, not the project: ``changes.patch`` applies to the original commit with
``git apply``, and ``CHANGE_SUMMARY.md`` is what a person reads before looking at the diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering_team.atomic_io import atomic_write_text
from engineering_team.git.port import Change, GitError
from engineering_team.modes.diff_noise import DiffNoise, measure, touched_scope
from engineering_team.modes.fix_report import render_fix_section
from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext

PATCH_FILE = "changes.patch"
SUMMARY_FILE = "CHANGE_SUMMARY.md"
MAX_FILES_LISTED = 60
MAX_SAID = 300


class ChangeError(RuntimeError):
    """The change cannot be measured (no base commit, not a repository)."""


@dataclass(frozen=True)
class Measured:
    base: str
    changes: list[Change]
    noise: DiffNoise


def base_commit(state: PipelineState) -> str:
    base = (state.isolation or {}).get("base_commit")
    if not base:
        raise ChangeError(
            "The run has no base commit to compare with (it was not started in a Git "
            "repository or a copy made with --init-git)."
        )
    return str(base)


def measure_change(ctx: RunContext, state: PipelineState) -> Measured:
    """The files changed since the team started and how much of that is outside its scope."""

    base = base_commit(state)
    try:
        changes = ctx.git.changes(base)
    except GitError as exc:
        raise ChangeError(f"The change could not be read from Git: {exc}") from exc
    scope = touched_scope(state.plan, state.profile)
    noise = measure(((c.path, c.lines) for c in changes), scope)
    return Measured(base, changes, noise)


def render_noise(noise: DiffNoise) -> list[str]:
    """The diff-noise section (lines, without a leading heading)."""

    if not noise.total_lines:
        return ["No lines changed."]
    lines = [
        f"{noise.outside_lines} of {noise.total_lines} changed line(s) "
        f"({noise.ratio:.0%}) are outside the places the plan and the tests account for "
        f"({noise.outside_files} file(s))."
    ]
    lines += [f"- `{item.path}`: {item.lines} line(s)" for item in noise.outside]
    if noise.outside_files > len(noise.outside):
        lines.append(f"- ... and {noise.outside_files - len(noise.outside)} more file(s)")
    return lines


def render_change_summary(ctx: RunContext, state: PipelineState, measured: Measured) -> str:
    iso = state.isolation or {}
    title = (
        state.spec.title
        if state.spec
        else state.triage.title
        if state.triage
        else ctx.settings.project_name
    )
    verdict = state.verification.verdict or "not verified"
    lines = [
        f"# Change summary: {title}",
        "",
        f"Run `{ctx.run_id}`. Verification: **{verdict}**. Written by the controller from Git and "
        "its own checks; the team's own words are at the end and are not evidence.",
        "",
        "## Where the work is",
        "",
        f"- Mode: {iso.get('mode', 'unknown')}; workspace `{ctx.workspace.root}`",
        f"- Your project: `{iso.get('source', '?')}` (branch `{iso.get('base_branch') or '-'}`)",
        f"- Team branch: `{iso.get('branch') or '(none: a copy)'}`, "
        f"started from `{measured.base[:12]}`",
        f"- Review: `engineering-team diff {ctx.run_id}`; take it with `engineering-team "
        f"export-patch {ctx.run_id} --out change.patch` and `git apply change.patch` on "
        f"`{measured.base[:12]}`, or merge the branch yourself. Nothing was pushed.",
    ]
    if state.fix is not None:
        lines += ["", *render_fix_section(state)]
    lines += ["", "## Files changed", ""]
    if measured.changes:
        lines += ["| File | Change | Lines |", "|---|---|---|"]
        for change in measured.changes[:MAX_FILES_LISTED]:
            counts = "binary" if change.added is None else f"+{change.added} -{change.removed}"
            lines.append(f"| `{change.path}` | {change.status} | {counts} |")
        if len(measured.changes) > MAX_FILES_LISTED:
            lines.append(f"| ... {len(measured.changes) - MAX_FILES_LISTED} more | | |")
    else:
        lines.append("No file changed.")
    lines += ["", "## Verification", ""]
    if state.checks:
        for result in state.checks:
            lines.append(
                f"- {result.name or result.id}: {result.status}. {result.summary}".rstrip()
            )
    else:
        lines.append("No checks were recorded.")
    if state.baseline is not None:
        lines.append(
            f"- Baseline: {len(state.baseline.known_failures)} failure(s) existed before the change"
        )
    for problem in state.verification.problems:
        lines.append(f"- Problem: {problem}")
    lines += ["", "## Review", ""]
    if state.findings:
        lines += [
            f"- {f.id} [{f.severity}] {f.file or '(project-wide)'}: {f.summary}"
            for f in state.findings
        ]
    else:
        lines.append("No findings (or no review ran).")
    lines += ["", "## Diff noise", "", *render_noise(measured.noise)]
    said = {k: v for k, v in state.summaries.items() if k != "summary" and v.strip()}
    if said:
        lines += ["", "## What the team said (not evidence)", ""]
        lines += [f"- {name}: {' '.join(text.split())[:MAX_SAID]}" for name, text in said.items()]
    return "\n".join(lines) + "\n"


@register_action("change_summary")
def change_summary_action(ctx: RunContext, state: PipelineState) -> str:
    """Write ``changes.patch`` and ``CHANGE_SUMMARY.md`` into the run directory."""

    try:
        measured = measure_change(ctx, state)
        patch = ctx.git.export_patch(ctx.run_dir / PATCH_FILE, measured.base)
    except (ChangeError, GitError) as exc:
        raise ChangeError(str(exc)) from exc
    state.diff_noise = measured.noise
    atomic_write_text(ctx.run_dir / SUMMARY_FILE, render_change_summary(ctx, state, measured))
    ctx.events.emit(
        "change.summary",
        files=len(measured.changes),
        lines=measured.noise.total_lines,
        outside_lines=measured.noise.outside_lines,
        patch=str(Path(patch).name),
    )
    return (
        f"{len(measured.changes)} file(s) changed, {measured.noise.total_lines} line(s), "
        f"{measured.noise.outside_lines} outside the plan's scope. {SUMMARY_FILE} and "
        f"{PATCH_FILE} are in the run directory."
    )
