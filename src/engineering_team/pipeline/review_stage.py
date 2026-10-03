"""Running the review stage: reviewers side by side, their findings merged, serious ones repaired.

The reviewers are read-only agents run through the parallel read-only helper (one lane each).
Each returns a ``ReviewReport``; the controller checks, merges, and numbers the findings
(``pipeline/review.py``), writes ``docs/review.md``, and hands findings at or above
``review.fail_on`` to the ``repair`` callback, which the executor wires to the verification loop
(one repair round from the shared budget, then the project is verified again).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from crewai.tools import BaseTool

from engineering_team.contracts import Finding, ReviewReport
from engineering_team.pipeline.parallel import JobResult, ReadOnlyJob, run_parallel_readonly
from engineering_team.pipeline.recipes import StageSpec
from engineering_team.pipeline.review import (
    Reviewed,
    blocking,
    consolidate,
    render_review,
)
from engineering_team.pipeline.stages import StageError, StageOutput
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.reports import REVIEW

REPORT_DIR = "docs/reviews"

# One reviewer's turn: (teammate, its read-only tools, lane) -> what it returned.
Call = Callable[[str, list[BaseTool], int], StageOutput]
# Sends the serious findings to repair and verifies again; returns a sentence for the report.
Repair = Callable[[list[Finding]], str]


def run_review(
    ctx: RunContext,
    state: PipelineState,
    stage: StageSpec,
    call: Call,
    repair: Repair | None,
    extra: Sequence[str] = (),
) -> str:
    """Run the stage; returns a one-line summary. Raises ``StageError`` if no reviewer reported."""

    team = [key for key in stage.teammates if ctx.team.usable(key)]
    jobs = [ReadOnlyJob(key, key, f"{REPORT_DIR}/{key}.md") for key in team]
    reports: dict[str, ReviewReport] = {}

    def runner(job: ReadOnlyJob, tools: list[BaseTool], lane: int) -> str:
        output = call(job.teammate, tools, lane)
        report = output.contracts.get("review")
        if not isinstance(report, ReviewReport):
            raise StageError(f"{job.teammate} returned no review report.")
        reports[job.name] = report
        return report.summary

    results = run_parallel_readonly(ctx, jobs, runner)
    reviews = [_reviewed(result, reports.get(result.name)) for result in results]
    if not any(r.report is not None for r in reviews):
        detail = "; ".join(f"{r.teammate}: {r.error}" for r in reviews)
        raise StageError(f"No reviewer reported ({detail or 'none enabled'}).")
    fail_on = ctx.settings.review.fail_on
    findings = consolidate(reviews)
    state.findings = findings
    ctx.reports.write(REVIEW, render_review(findings, reviews, fail_on=fail_on, extra=extra))
    serious = blocking(findings, fail_on)
    ctx.events.emit(
        "review.findings",
        reviewers=[r.teammate for r in reviews if r.report is not None],
        total=len(findings),
        serious=[f.id for f in serious],
    )
    outcome = ""
    if serious and repair is not None:
        outcome = repair(serious)
        ctx.reports.write(
            REVIEW, render_review(findings, reviews, fail_on=fail_on, outcome=outcome, extra=extra)
        )
    elif serious:
        outcome = "No verify stage in this recipe, so nothing was sent to repair."
    done = sum(1 for r in reviews if r.report is not None)
    return (
        f"{done} reviewer(s) reported {len(findings)} finding(s), {len(serious)} at "
        f"{fail_on} or above. {outcome}".strip()
    )


def _reviewed(result: JobResult, report: ReviewReport | None) -> Reviewed:
    if result.status != "succeeded" or report is None:
        return Reviewed(result.name, error=result.error or "no report")
    return Reviewed(result.name, report)
