"""A recipe's policies as check results, so the verify stage treats them like any check."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from engineering_team.contracts import CheckResult
from engineering_team.git.port import GitError
from engineering_team.modes.policies import evaluate
from engineering_team.runtime.context import RunContext
from engineering_team.verification.revision import verification_revision

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

POLICY_PREFIX = "policy:"
MAX_LISTED = 40


def policy_results(
    ctx: RunContext, state: PipelineState, names: Sequence[str]
) -> list[CheckResult]:
    """One required result per policy, measured from Git against the commit the team started
    from. A policy that cannot be measured (no starting commit) is ``unavailable``, never a pass."""

    if not names:
        return []
    base = (state.isolation or {}).get("base_commit")
    revision = verification_revision(ctx.workspace)
    try:
        if not base:
            raise GitError("the run has no starting commit to measure the change against")
        changes = ctx.git.changes(str(base))
    except GitError as exc:
        return [
            CheckResult(
                id=f"{POLICY_PREFIX}{name}",
                status="unavailable",
                name=f"Policy {name}",
                source="plan",
                summary="could not be measured",
                hint=str(exc),
                revision=revision,
            )
            for name in names
        ]
    results: list[CheckResult] = []
    for outcome in evaluate(names, changes, ctx.settings):
        listed = outcome.violations[:MAX_LISTED]
        more = len(outcome.violations) - len(listed)
        results.append(
            CheckResult(
                id=f"{POLICY_PREFIX}{outcome.name}",
                status="passed" if outcome.ok else "failed",
                name=f"Policy {outcome.name}",
                source="plan",
                summary=(
                    "the change stays within it"
                    if outcome.ok
                    else f"{len(outcome.violations)} violation(s)"
                ),
                hint=None if outcome.ok else "Revert the changes listed below (or undo them).",
                log_tail="\n".join([*listed, *([f"... and {more} more"] if more else [])]),
                revision=revision,
            )
        )
    ctx.events.emit(
        "verify.policies",
        policies={r.id.removeprefix(POLICY_PREFIX): r.status for r in results},
    )
    return results
