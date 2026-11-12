"""What the results page shows beyond the report's tables: the answer with its checked sources,
the escalation timeline, the attempts side by side, and the thread the run is in.

``GET /runs/{id}/results`` carries these as ``views`` next to the report's own data. They are
made here, from the controller's records (``ladder.json``, ``attempts.json``, the answer and
its citation check, the manifests of the thread); the page only lays them out. Text an agent wrote
(an answer, a judge's reason) is kept apart from what the controller measured and shown as text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engineering_team.modes.answer_contracts import PLAN_FILE
from engineering_team.modes.threads import BUSY, ThreadError, implementation_mode, load_parent
from engineering_team.pipeline.attempts_contracts import AttemptsRecord
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.report import attempts_view
from engineering_team.report.fmt import money
from engineering_team.report.model import RunReport
from engineering_team.runtime.run_index import RunRef, find_runs, thread_of
from engineering_team.ui.triage_view import RUNG_LABELS

MAX_PLAN_CHARS = 60_000
MAX_NOTE = 600
CITE = re.compile(r"\[(\d{1,3})\]")
TONES = {"succeeded": "good", "escalated": "warn", "running": "info"}


def build(report: RunReport, ref: RunRef, workspace_root: str) -> dict[str, Any]:
    """Every view of a run's results page (``None`` for a part the run did not have)."""

    return {
        "answer": answer_view(report, ref),
        "escalation": escalation_view(report.ladder),
        "attempts": attempts_panel(report.attempts),
        "thread": thread_view(ref, workspace_root),
    }


# -- the answer ----------------------------------------------------------------------------------


def with_citations(text: str, count: int) -> list[dict[str, Any]]:
    """``text`` as parts: plain text and ``[n]`` markers that name one of ``count`` sources."""

    parts: list[dict[str, Any]] = []
    last = 0
    for match in CITE.finditer(text):
        number = int(match.group(1))
        if not 1 <= number <= count:
            continue
        if match.start() > last:
            parts.append({"text": text[last : match.start()]})
        parts.append({"cite": number})
        last = match.end()
    if last < len(text):
        parts.append({"text": text[last:]})
    return parts


def answer_view(report: RunReport, ref: RunRef) -> dict[str, Any] | None:
    """The answer path's result (``ANSWER.md``), or the plan a plan-only run stored."""

    found, check = report.answer, report.answer_check
    if found is not None and check is not None:
        results = {r.number: r for r in check.results}
        citations = []
        for number, citation in enumerate(found.citations, 1):
            result = results.get(number)
            citations.append(
                {
                    "number": number,
                    "kind": citation.kind,
                    "target": citation.target,
                    "title": citation.title,
                    "quote": " ".join(citation.quote.split()),
                    "status": result.status if result else "unchecked",
                    "ok": bool(result and result.ok),
                    "detail": result.detail if result else "",
                }
            )
        bad = [c for c in citations if not c["ok"]]
        total = len(citations)
        return {
            "kind": "answer",
            "summary": found.summary,
            "summary_parts": with_citations(found.summary, total),
            "confidence": found.confidence,
            "sections": [
                {"heading": s.heading, "parts": with_citations(s.body, total)}
                for s in found.sections
            ],
            "citations": citations,
            "verified": total - len(bad),
            "total": total,
            "tone": "good" if not bad else "bad",
            "headline": (
                f"{total - len(bad)} of {total} citation(s) verified by the controller"
                if total
                else "The answer cites nothing, so nothing in it was checked"
            ),
            "open_questions": list(found.open_questions),
            "changed": list(check.changed),
            "note": "The controller checked each source against the project and the run's tool "
            "calls. The confidence is the agent's own, not a measurement.",
        }
    plan = ref.run_dir / PLAN_FILE
    if plan.is_file():
        text = plan.read_text(encoding="utf-8", errors="replace")
        cut = len(text) > MAX_PLAN_CHARS
        return {
            "kind": "plan",
            "text": text[:MAX_PLAN_CHARS],
            "truncated": cut,
            "note": "A plan only: nothing was implemented. Follow it up to carry it out.",
        }
    return None


# -- escalation ----------------------------------------------------------------------------------


def escalation_view(ladder: LadderRecord | None) -> dict[str, Any] | None:
    """The rungs an ``auto`` run climbed, in order, with why it moved and what each cost."""

    if ladder is None:
        return None
    rungs: list[dict[str, Any]] = []
    for rung in ladder.rungs:
        seconds = (
            round((rung.finished - rung.started).total_seconds(), 1) if rung.finished else None
        )
        rungs.append(
            {
                "rung": rung.rung,
                "label": RUNG_LABELS.get(rung.rung, rung.rung),
                "status": rung.status,
                "tone": TONES.get(rung.status, "info"),
                "recipe": rung.recipe,
                "why": rung.why,
                "outcome": rung.outcome,
                "failing_checks": list(rung.failing_checks),
                "cost": money(rung.cost_usd) if rung.cost_usd is not None else "",
                "estimate": money(rung.estimate_usd) if rung.estimate_usd is not None else "",
                "seconds": seconds,
                "handed_on": rung.escalation[:MAX_NOTE],
            }
        )
    path = " → ".join(r["rung"] for r in rungs)
    climbed = len(rungs) > 1
    return {
        "start": ladder.start,
        "max_rung": ladder.max_rung,
        "reasons": list(ladder.reasons),
        "climbed": climbed,
        "headline": (
            f"Escalated: {path}"
            if climbed
            else f"Stayed on the {ladder.start} rung: no escalation was needed"
        ),
        "rungs": rungs,
        "stopped": ladder.stopped,
    }


# -- attempts --------------------------------------------------------------------------------------


def attempts_panel(record: AttemptsRecord | None) -> dict[str, Any] | None:
    """The attempts side by side, the judge's reason, and the cost ledger."""

    if record is None:
        return None
    selection = record.selection
    chosen = selection.chosen if selection else None
    cards: list[dict[str, Any]] = []
    for a in record.attempts:
        cards.append(
            {
                "label": a.label,
                "candidate": selection.anonymised.get(a.label, "") if selection else "",
                "chosen": a.label == chosen,
                "vary": a.vary,
                "status": a.status,
                "tone": {"passed": "good", "failed": "bad"}.get(a.status, "info"),
                "outcome": a.outcome,
                "checks": f"{a.checks_passed} of {a.checks_total}" if a.checks_total else "none",
                "failing_checks": list(a.failing_checks),
                "findings": a.findings,
                "files_changed": a.files_changed,
                "diff_lines": a.diff_lines,
                "cost": money(a.cost_usd),
                "patch": a.patch,
                "scores": _scores(selection, a.label),
            }
        )
    return {
        "headline": attempts_view.summary_line(record).replace("**", ""),
        "count": record.count,
        "vary": record.vary,
        "source": record.source,
        "method": selection.method if selection else "",
        "method_text": attempts_view.METHOD[selection.method] if selection else "not finished",
        "chosen": chosen,
        "reason": selection.reason if selection else "",
        "judge": {
            "model": selection.judge_model,
            "error": selection.judge_error,
            "cost": money(selection.judge_cost_usd),
        }
        if selection and (selection.judge_model or selection.judge_error)
        else None,
        "attempts": cards,
        # The reason and the judge have their own place on the page; the rest are notes.
        "notes": [n for n in attempts_view.notes(record) if not n.startswith(("Why:", "Judge:"))],
        "ledger": attempts_view.ledger_line(record),
        "stopped": record.stopped,
    }


def _scores(selection: Any, label: str) -> dict[str, int] | None:
    if selection is None or label not in selection.scores:
        return None
    score = selection.scores[label]
    return {
        "correctness": score.correctness,
        "minimality": score.minimality,
        "consistency": score.consistency,
        "tests": score.tests,
        "total": score.total,
    }


# -- the thread -----------------------------------------------------------------------------------


def head_of(run_dir: Path) -> str:
    try:
        text = (run_dir / "request.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    return " ".join(text.split("\n---\n")[0].split())[:100]


def thread_view(ref: RunRef, workspace_root: str) -> dict[str, Any]:
    """The run's thread (every run that follows from the same first one, oldest first) and what
    the follow-up box may do for this run."""

    runs = thread_of(find_runs(workspace_root), ref)
    status = ref.manifest.status
    can_continue = status not in BUSY and ref.workspace.is_dir()
    can_implement = False
    if can_continue:
        try:
            implementation_mode(load_parent(ref))
            can_implement = True
        except ThreadError:
            can_implement = False
    return {
        "thread_id": ref.thread_id,
        "runs": [
            {
                "run_id": r.run_id,
                "current": r.run_id == ref.run_id,
                "parent": r.parent_run,
                "status": r.manifest.status,
                "verdict": r.manifest.verdict,
                "request": head_of(r.run_dir),
            }
            for r in runs
        ],
        "in_thread": len(runs) > 1,
        "can_continue": can_continue,
        "can_implement": can_implement,
        "why_not": ""
        if can_continue
        else f"The run is still {status}; follow it up once it ends."
        if status in BUSY
        else "The run's workspace is gone, so there is no branch to continue on.",
    }
