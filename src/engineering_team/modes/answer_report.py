"""``ANSWER.md`` and ``PLAN.md``: the answer path's documents, in the run directory.

The answer is the agent's; the verification table under it is the controller's. A citation that
did not hold is marked in the text and listed, so a reader sees which claims rest on evidence.
"""

from __future__ import annotations

from engineering_team.contracts import Plan, Spec
from engineering_team.modes.answer_contracts import Answer, AnswerCheck

MARK = {True: "verified", False: "NOT VERIFIED"}


def render_answer(answer: Answer, check: AnswerCheck, verdict: str, request: str) -> str:
    lines = ["# Answer", "", f"> {_first_line(request)}", "", answer.summary.strip(), ""]
    for section in answer.sections:
        lines += [f"## {section.heading.strip()}", "", section.body.strip(), ""]
    lines += ["## Sources", ""]
    if not answer.citations:
        lines += ["The answer cites nothing.", ""]
    for citation, result in zip(answer.citations, check.results, strict=True):
        note = "" if result.ok else f": {result.detail or result.status}"
        lines.append(f"{result.number}. `{result.target}` ({MARK[result.ok]}{note})")
        if citation.kind == "web" and citation.title:
            lines.append(f"   {citation.title}")
        if citation.quote.strip():
            quoted = " ".join(citation.quote.split())
            lines.append(f"   > {quoted}")
    if answer.open_questions:
        lines += ["", "## Open questions", "", *(f"- {q}" for q in answer.open_questions)]
    total = len(check.results)
    lines += [
        "",
        "## Verification",
        "",
        f"Verdict: **{verdict}**. {check.verified} of {total} citation(s) checked by the "
        f"controller against the project and the run's tool calls; the answer's own confidence "
        f"is {answer.confidence}.",
    ]
    if check.changed:
        lines.append(f"The project changed during this read-only run: {', '.join(check.changed)}.")
    return "\n".join(lines).rstrip() + "\n"


def render_plan(spec: Spec | None, plan: Plan | None, request: str, changed: list[str]) -> str:
    lines = ["# Plan", "", f"> {_first_line(request)}", ""]
    if spec is not None:
        lines += [f"## {spec.title}", "", spec.summary.strip(), ""]
        if spec.criteria:
            lines += ["### Acceptance criteria", ""]
            lines += [f"- **{c.id}** {c.text}" for c in spec.criteria]
            lines.append("")
        if spec.non_goals:
            lines += ["### Out of scope", "", *(f"- {g}" for g in spec.non_goals), ""]
    if plan is not None:
        lines += ["## Impact and work packages", ""]
        if plan.stack:
            lines += [f"Stack: {plan.stack}", ""]
        for package in plan.work_packages:
            owned = ", ".join(f"`{p}`" for p in package.owned_paths) or "(not named)"
            lines.append(f"- **{package.id} {package.title}** ({package.role}): {owned}")
        lines.append("")
        lines += ["## Risks", "", *(f"- {r}" for r in plan.risks or ["None named."]), ""]
    if spec is not None and spec.open_questions:
        lines += ["## Open questions", "", *(f"- {q}" for q in spec.open_questions), ""]
    lines.append("Nothing was implemented: this is a plan only.")
    if changed:
        lines.append(f"The project changed during this run: {', '.join(changed)}.")
    return "\n".join(lines).rstrip() + "\n"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""
