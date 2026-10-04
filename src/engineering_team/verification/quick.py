"""One verification pass without the repair loop, the report, or a verdict for the run.

``upgrade-deps`` asks "does the project still pass its own checks?" once per group of upgrades and
undoes the group when it does not. The question is answered the way the verify stage answers it
(the project's detected checks and the user's pinned ones, judged against the baseline), but
nothing is recorded in the run's verification state: only the next group's outcome matters.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from engineering_team.contracts import CheckResult
from engineering_team.runtime.context import RunContext
from engineering_team.verification.baseline_compare import apply_baseline
from engineering_team.verification.checks_file import ChecksFileError, pinned_checks
from engineering_team.verification.profiles import build_checks
from engineering_team.verification.revision import verification_revision
from engineering_team.verification.verdict import judge
from engineering_team.verification.verifier import Verifier

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

MAX_REASON = 400


def quick_verify(ctx: RunContext, state: PipelineState, label: str) -> tuple[bool, str]:
    """Run the checks once. Returns ``(ok, reason)``: ``ok`` only when every required check
    passed on the project as it is now; ``reason`` says which check did not and what it showed."""

    try:
        user = pinned_checks(ctx, state.verification.checks_digest)
    except ChecksFileError as exc:
        return False, str(exc)[:MAX_REASON]
    checks = build_checks(ctx, None, user).checks
    results = Verifier(ctx).run(checks, label=label)
    if state.baseline is not None:
        results = apply_baseline(results, {c.id: c.cwd for c in checks}, state.baseline)
    judgement = judge(results, verification_revision(ctx.workspace))
    if judgement.verdict == "verified":
        return True, ""
    return False, _reason(results, judgement.problems)


def _detail(result: CheckResult) -> str:
    """What a failed check showed: the failing tests by id when the report has them, else the
    end of its output starting at a word."""

    failures = (result.report or {}).get("failures") or []
    named = []
    for item in failures[:3]:
        if isinstance(item, dict) and item.get("test_id"):
            message = " ".join(str(item.get("message") or "").split())[:80]
            named.append(f"{item['test_id']} ({message})" if message else str(item["test_id"]))
    if named:
        return "; ".join(named)
    tail = " ".join((result.log_tail or "").split())[-200:]
    return tail.split(" ", 1)[-1] if " " in tail else tail


def _reason(results: Sequence[CheckResult], problems: Sequence[str]) -> str:
    failing = [r for r in results if r.required and r.status != "passed"]
    if not failing:
        return "; ".join(problems)[:MAX_REASON]
    parts = []
    for result in failing:
        detail = _detail(result)
        parts.append(
            f"{result.id}: {result.summary or result.status}" + (f" | {detail}" if detail else "")
        )
    return "; ".join(parts)[:MAX_REASON]
