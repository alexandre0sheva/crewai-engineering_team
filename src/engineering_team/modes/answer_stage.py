"""Running the ``answer`` stage: a read-only agent answers, the controller checks the citations.

The agent gets read-only tools (and the web tools when ``web.enabled`` and its group allow them).
Its answer is checked against the project and the run's tool calls (``modes/citations.py``); when
some citations do not hold it gets one more turn, told which and why. ``ANSWER.md`` is written
either way, with the failed citations marked, and the run's verdict follows the check: ``verified``
only when every citation holds. A project that changed during the run is a failed run.
"""

from __future__ import annotations

from collections.abc import Callable

from engineering_team.modes.answer_contracts import ANSWER_FILE, Answer, AnswerCheck
from engineering_team.modes.answer_report import render_answer
from engineering_team.modes.citations import fetched_urls, verdict_of, verify_citations
from engineering_team.pipeline.actions import register_action
from engineering_team.pipeline.stages import StageError, StageOutput
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.snapshot import Snapshot

ANSWER_REPAIRS = 1  # more turns for the agent when citations do not hold
SCRATCH = ".engineering-team/tmp/answer.md"  # the one path the answer stage's agent may write

Call = Callable[[str], StageOutput]


def changed_files(ctx: RunContext) -> list[str]:
    """Project files added, changed or removed since the run started (controller state is not
    part of the project)."""

    before, after = ctx.baseline.files, Snapshot.take(ctx.workspace).files
    names = (set(before) ^ set(after)) | {
        n for n in set(before) & set(after) if before[n].sha256 != after[n].sha256
    }
    return sorted(names)


def citation_note(check: AnswerCheck) -> str:
    """What the agent is told when citations did not hold."""

    lines = [
        "The previous answer had citations the controller could not verify. Fix each (read the "
        "file again and quote it exactly, or fetch the page) or drop the claim:"
    ]
    lines += [f"- [{r.number}] {r.target}: {r.detail or r.status}" for r in check.unverified]
    return "\n".join(lines)


def run_answer(ctx: RunContext, state: PipelineState, call: Call) -> str:
    """Get the answer, check it, write ``ANSWER.md``; ends the run unverified when it did not hold.
    Returns a one-line summary for the stage record."""

    from engineering_team.verification.loop import VerificationError

    note = ""
    for attempt in range(1, ANSWER_REPAIRS + 2):
        output = call(note)
        found = output.contracts.get("answer")
        if not isinstance(found, Answer):
            raise StageError("The answer stage did not return a valid answer.")
        answer = found
        fetched = fetched_urls(ctx.run_dir / "trajectory.jsonl")
        check = AnswerCheck(
            results=verify_citations(answer, ctx.workspace.root, fetched),
            changed=changed_files(ctx),
            attempts=attempt,
        )
        ctx.events.emit(
            "answer.checked",
            attempt=attempt,
            citations=len(check.results),
            verified=check.verified,
        )
        if not check.unverified or check.changed or attempt > ANSWER_REPAIRS:
            break
        note = citation_note(check)
    verdict, problems = verdict_of(check)
    state.answer, state.answer_check = answer, check
    state.verification.verdict = verdict  # type: ignore[assignment]
    state.verification.problems = problems
    path = ctx.run_dir / ANSWER_FILE
    path.write_text(render_answer(answer, check, verdict, state.request_head), encoding="utf-8")
    ctx.events.emit("answer.written", verdict=verdict, path=ANSWER_FILE)
    if verdict != "verified":
        raise VerificationError(
            f"The answer is {verdict}: {problems[0]} (see {ANSWER_FILE} in the run directory)."
        )
    return (
        f"{check.verified} of {len(check.results)} citation(s) verified; written to {ANSWER_FILE}."
    )


@register_action("plan_report")
def plan_report(ctx: RunContext, state: PipelineState) -> str:
    """Write ``PLAN.md`` from the spec and the plan, and require that nothing was implemented."""

    from engineering_team.modes.answer_contracts import PLAN_FILE
    from engineering_team.modes.answer_report import render_plan
    from engineering_team.verification.loop import VerificationError

    changed = changed_files(ctx)
    text = render_plan(state.spec, state.plan, state.request_head, changed)
    (ctx.run_dir / PLAN_FILE).write_text(text, encoding="utf-8")
    if changed:
        state.verification.verdict = "failed"
        state.verification.problems = [f"changed during a plan-only run: {', '.join(changed[:5])}"]
        raise VerificationError(
            f"A plan-only run changed the project ({', '.join(changed[:5])}); see {PLAN_FILE}."
        )
    return f"Plan written to {PLAN_FILE} (nothing implemented)."
