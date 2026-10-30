"""What the lead asks a teammate for: briefs, the jobs the controller runs, and the results.

The checks and the running are the delegator's (``pipeline/delegation.py``); this module parses a
``team`` tool call into briefs, turns a brief into the text a teammate's prompt carries, and
renders a finished job as the compact result the lead reads (the controller's facts first, the
teammate's own words labelled as such).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from engineering_team.pipeline.lead_contracts import DelegationKind, DelegationRecord
from engineering_team.pipeline.stages import StageOutput
from engineering_team.tools.support import ToolError

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext

# Who a typed ask goes to, and the prompt (config/stages.yaml) each kind of job runs.
ASKED: dict[str, str] = {
    "review": "code_reviewer",
    "security_review": "security_engineer",
    "debug": "debugger",
    "architect": "solution_architect",
    "analyst": "product_analyst",
}
PROMPTS: dict[str, str] = {
    "delegate": "delegate_work",
    "review": "delegate_review",
    "security_review": "delegate_review",
    "debug": "delegate_debug",
    "architect": "delegate_consult",
    "analyst": "delegate_consult",
}
KIND_OF_TOOL: dict[str, DelegationKind] = {
    "Delegate": "delegate",
    "Delegate Parallel": "delegate",
    "Ask Reviewer": "review",
    "Ask Security Review": "security_review",
    "Ask Debugger": "debug",
    "Ask Architect": "architect",
    "Ask Analyst": "analyst",
}
CAP_BY_SIZE = {"small": 2, "medium": 5, "large": 12}
MAX_RESULT = 6000  # characters handed back to the lead (about 1,500 tokens)
MAX_BRIEF = 4000
MAX_FILES = 20
MAX_FINDINGS = 8


class Brief(BaseModel):
    """One job as the lead asked for it (``Delegate``, or one entry of ``Delegate Parallel``)."""

    model_config = ConfigDict(extra="ignore")

    teammate: str
    brief: str = Field(min_length=1, max_length=MAX_BRIEF)
    owned_paths: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)
    max_steps: int = 0
    why: str = ""

    @field_validator("teammate")
    @classmethod
    def _key(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")


@dataclass(frozen=True)
class Job:
    """What the executor runs for one delegation: the teammate, its brief and its limits."""

    id: str
    kind: DelegationKind
    teammate: str
    prompt: str
    brief: str  # the text the teammate's prompt carries
    owned_paths: tuple[str, ...]  # empty: the job only reads
    max_iter: int
    card_id: str | None

    @property
    def lane(self) -> str:
        return self.id


# Runs one job as a small crew and returns what its agent produced (a review: the report).
JobRunner = Callable[[Job], StageOutput]


def delegation_cap(ctx: RunContext, size: str | None) -> int:
    """How many jobs the lead may start: ``delegation.max_delegations``, or by size."""

    configured = ctx.settings.delegation.max_delegations
    if configured != "auto":
        return int(configured)
    return CAP_BY_SIZE.get(size or "medium", CAP_BY_SIZE["medium"])


def parse_briefs(
    tool: str, kind: DelegationKind, arguments: dict[str, Any], state: PipelineState
) -> list[Brief]:
    """The jobs one ``team`` tool call asks for (a ``ToolError`` says what is wrong)."""

    try:
        if tool == "Delegate Parallel":
            raw = arguments.get("briefs") or []
            if not isinstance(raw, list) or not raw:
                raise ToolError("Give briefs as a list of {teammate, brief, owned_paths}.")
            return [Brief.model_validate(item) for item in raw]
        if kind == "delegate":
            return [Brief.model_validate(arguments)]
        return [_ask(kind, arguments, state)]
    except ValidationError as exc:
        problem = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
        )
        raise ToolError(f"Invalid brief ({problem}).") from None


def _ask(kind: DelegationKind, arguments: dict[str, Any], state: PipelineState) -> Brief:
    why = str(arguments.get("why") or "")
    if kind in ("review", "security_review"):
        focus = str(arguments.get("focus") or "").strip()
        text = f"Focus on: {focus}" if focus else "Review the whole change made so far."
        return Brief(teammate=ASKED[kind], brief=text, why=why)
    if kind == "debug":
        check_id = str(arguments.get("check_id") or "").strip()
        if not check_id:
            raise ToolError("Name the failing check (check_id) the debugger should look at.")
        known = {check.id: check for check in state.checks}
        if known and check_id not in known:
            raise ToolError(f"No controller check '{check_id}'. Known checks: {', '.join(known)}.")
        found = known.get(check_id)
        detail = f" ({found.status}: {found.summary})" if found else ""
        paths = [str(p) for p in arguments.get("paths") or []]
        text = f"The check '{check_id}' fails{detail}."
        return Brief(teammate=ASKED[kind], brief=text, owned_paths=paths, why=why)
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise ToolError("Ask a question (or give the plan draft to decide on).")
    return Brief(teammate=ASKED[kind], brief=question, why=why)


def one_line(record: DelegationRecord) -> str:
    cost = f"${record.cost_usd:.4f}" if record.cost_usd is not None else "cost unknown"
    files = f", {len(record.files_changed)} file(s) changed" if record.files_changed else ""
    found = f", {len(record.findings)} finding(s)" if record.findings else ""
    return (
        f"{record.id} {record.teammate} {record.status} in {record.seconds:.0f} s: "
        f"{record.calls} model call(s), "
        f"{record.tokens} tokens, {cost}{files}{found}."
    )


def render_result(record: DelegationRecord) -> str:
    lines = [one_line(record)]
    if record.error:
        lines.append(f"Error: {record.error}")
    if record.files_changed:
        shown = record.files_changed[:MAX_FILES]
        more = len(record.files_changed) - len(shown)
        lines.append("Files changed: " + ", ".join(shown) + (f" and {more} more" if more else ""))
    if record.commands:
        lines.append("Commands it ran: " + "; ".join(record.commands))
    for finding in record.findings[:MAX_FINDINGS]:
        where = f" {finding.file}:{finding.line or ''}".rstrip(":") if finding.file else ""
        fix = f" Fix: {finding.suggested_fix}" if finding.suggested_fix else ""
        lines.append(f"- [{finding.severity}]{where} {finding.summary}{fix}")
    if len(record.findings) > MAX_FINDINGS:
        lines.append(f"- ... and {len(record.findings) - MAX_FINDINGS} more findings")
    if record.summary:
        said = " ".join(record.summary.split())
        lines.append(f"What it said (its own words, not evidence): {said}")
    return "\n".join(lines)


def brief_text(kind: DelegationKind, brief: Brief) -> str:
    """The job as the teammate's prompt carries it (``{brief}``)."""

    lines = [brief.brief.strip()]
    if kind in ("delegate", "debug"):
        lines.append(
            f"You may change only: {', '.join(brief.owned_paths)}. Reading is unrestricted."
            if brief.owned_paths
            else "You can only read: diagnose, do not change any file."
        )
    if brief.criteria:
        lines.append(f"Acceptance criteria to deliver: {', '.join(brief.criteria)}.")
    if brief.why:
        lines.append(f"Why the lead asks: {brief.why}")
    return "\n".join(lines)
