"""The controller actions behind the read-only ``review`` recipe.

``review_target`` decides what is being reviewed: everything since the base branch's merge base
(``--base``), or the uncommitted changes of a dirty tree, or, with neither, the work since the
repository's default branch. ``review_gate`` writes ``findings.json`` and ``findings.md`` and
ends the run ``failed`` (exit code 3, verdict ``failed``) when a finding reaches ``review.fail_on``,
so the command can gate a pipeline.
"""

from __future__ import annotations

from engineering_team.git.port import GitError
from engineering_team.modes.findings_report import gate
from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext

DEFAULT_BASES = ("origin/HEAD", "origin/main", "origin/master", "main", "master", "trunk")


class ReviewError(RuntimeError):
    """Nothing can be reviewed here; the message says what to pass instead."""


@register_action("review_target")
def review_target(ctx: RunContext, state: PipelineState) -> str:
    git = ctx.git
    try:
        if not git.is_repo():
            raise ReviewError("review needs a Git repository (the project's top level).")
        head = git.head()
        if head is None:
            raise ReviewError("the repository has no commits yet, so there is nothing to compare.")
        asked = state.options.get("base")
        label: str | None
        if asked:
            if not git.resolves(str(asked)):
                raise ReviewError(
                    f"--base {asked!r} is not a branch, tag, or commit in this repository."
                )
            base, label = git.merge_base(str(asked)), str(asked)
        elif git.is_dirty():
            base, label = head, "HEAD (your uncommitted changes)"
        else:
            base, label = None, None
            for candidate in DEFAULT_BASES:
                if git.resolves(candidate) and (point := git.merge_base(candidate)) != head:
                    base, label = point, candidate
                    break
        changes = git.changes(base) if base else []
    except GitError as exc:
        raise ReviewError(str(exc)) from exc
    state.isolation = {
        "mode": "review",
        "source": str(ctx.workspace.root),
        "base_commit": base,
        "base_branch": label,
        "empty": not changes,
    }
    if not changes:
        return (
            "Nothing to review: no change against "
            + (label or "any base branch")
            + ". Pass --base."
        )
    lines = sum(c.lines for c in changes)
    return f"Reviewing {len(changes)} changed file(s), {lines} line(s), against {label}."


@register_action("review_gate")
def review_gate(ctx: RunContext, state: PipelineState) -> str:
    return gate(ctx, state, kind="review", title="Review findings")
