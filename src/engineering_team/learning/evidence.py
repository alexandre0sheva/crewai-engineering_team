"""What a reflection reads about a run: the controller's own records, compactly.

The verdict and the latest check results (``state.json``), every check run in order with the stage
it ran in (``check.finished`` events), the repair rounds, the escalation ladder's rungs
(``ladder.json``), the stages, a summary of the trajectory (one line per tool call, the errors
and the agents' final answers, capped at ``learning.trace_chars``), and the guidance of the
harness the agents worked under (each teammate's tool guidance and guardrails, the skills it
loaded). Everything the agents said is labelled theirs; the reflection's evidence ids (check ids,
``#seq`` trajectory entries) are checked against these records before a lesson is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engineering_team.harness.loader import COMPONENTS
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import EVENTS_FILENAME, RunStore
from engineering_team.runtime.trace import ordered
from engineering_team.runtime.trajectory import TrajectoryEntry, read_trajectory

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

MAX_REQUEST_CHARS = 1500
MAX_LINE = 160
MAX_GUIDANCE_CHARS = 700
MAX_FINAL_CHARS = 300
MAX_HISTORY = 40


def _line(text: str, limit: int = MAX_LINE) -> str:
    squeezed = " ".join(str(text).split())
    return squeezed if len(squeezed) <= limit else squeezed[: limit - 3] + "..."


@dataclass
class RunEvidence:
    """The records of one run a reflection may cite."""

    run_id: str
    domain: str
    mode: str
    verdict: str
    request: str = ""
    checks: list[str] = field(default_factory=list)  # the latest result of every check
    history: list[str] = field(default_factory=list)  # every check run, in order
    repairs: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    trace: str = ""
    guidance: dict[str, str] = field(default_factory=dict)  # teammate -> its guidance text
    skills_loaded: list[str] = field(default_factory=list)
    check_ids: set[str] = field(default_factory=set)
    seqs: set[int] = field(default_factory=set)
    revision: str = ""

    @property
    def has_checks(self) -> bool:
        """The controller ran at least one check: a verdict with evidence behind it."""

        return bool(self.check_ids)

    def text(self) -> str:
        """The evidence as the reflection's prompt body."""

        parts = [
            f"Run {self.run_id}: mode {self.mode}, domain {self.domain}, verdict {self.verdict}.",
            "",
            "Request (data):",
            "<<<REQUEST",
            self.request,
            "REQUEST>>>",
            "",
            "Stages (the controller's record):",
            *(f"- {line}" for line in self.stages or ["(none)"]),
            "",
            "Checks the controller ran, in order (stage, check id, result):",
            *(f"- {line}" for line in self.history or ["(none)"]),
            "",
            "Latest result of each check (check ids you may cite):",
            *(f"- {line}" for line in self.checks or ["(none)"]),
        ]
        if self.repairs:
            parts += ["", "Repair rounds:", *(f"- {line}" for line in self.repairs)]
        if self.escalations:
            parts += ["", "Escalation ladder:", *(f"- {line}" for line in self.escalations)]
        parts += ["", "Harness guidance the agents worked under (data):"]
        for key, text in sorted(self.guidance.items()):
            parts.append(f"- teammates/{key}: {text}")
        if self.skills_loaded:
            parts.append(f"- skills loaded: {', '.join(self.skills_loaded)}")
        parts += [
            "",
            "Trajectory summary (#seq teammate tool(args) -> result; agents' words are theirs):",
            self.trace or "(nothing recorded)",
        ]
        return "\n".join(parts)


def _entry_line(entry: TrajectoryEntry) -> str:
    who = entry.teammate or "?"
    if entry.kind == "message":
        return f"#{entry.seq} {who} said (final): {_line(entry.text, MAX_FINAL_CHARS)}"
    if entry.kind == "notice":
        return f"#{entry.seq} notice: {_line(entry.text)}"
    args = ", ".join(f"{k}={_line(str(v), 60)}" for k, v in entry.args.items())
    head = entry.output.strip().splitlines()[0] if entry.output.strip() else "(empty)"
    result = "OK" if entry.ok else "ERROR"
    return f"#{entry.seq} {who} {entry.tool}({_line(args, 100)}) -> {result}: {_line(head, 100)}"


def trace_summary(entries: list[TrajectoryEntry], limit: int) -> str:
    """One line per tool call and final answer, stage by stage; over ``limit`` characters the
    middle of the run is left out (its start and its end say most)."""

    lines: list[str] = []
    stage: str | None = None
    for entry in ordered(entries):
        if entry.kind == "message" and entry.role != "final":
            continue
        if entry.stage != stage:
            stage = entry.stage
            lines.append(f"== stage {stage or '(none)'} ==")
        lines.append(_entry_line(entry))
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2 :]
    head, tail = head[: head.rfind("\n")], tail[tail.find("\n") + 1 :]
    return f"{head}\n... [{len(text) - limit:,} characters of the middle left out] ...\n{tail}"


def _guidance(ctx: RunContext, teammates: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in sorted(teammates):
        fields = ctx.harness.teammates.get(key, {})
        texts = [
            f"{name}: {_line(fields[name], MAX_GUIDANCE_CHARS)}"
            for name in COMPONENTS
            if isinstance(fields.get(name), str) and fields[name].strip()
        ]
        if texts:
            found[key] = " | ".join(texts)
    return found


def gather(ctx: RunContext, verdict: str) -> RunEvidence:
    """The run's evidence from its directory (missing files leave their part empty)."""

    run_dir = ctx.run_dir
    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    evidence = RunEvidence(
        run_id=ctx.run_id, domain=ctx.harness.domain.key, mode=manifest.mode, verdict=verdict
    )
    try:
        request = (run_dir / "request.md").read_text(encoding="utf-8").strip()
    except OSError:
        request = ""
    evidence.request = request[:MAX_REQUEST_CHARS]
    evidence.stages = [
        f"{s.name}: {s.status}" + (f" (attempts {s.attempts})" if s.attempts > 1 else "")
        for s in manifest.stages
    ]
    state = PipelineState.load(run_dir)
    if state is not None:
        for check in state.checks:
            evidence.check_ids.add(check.id)
            summary = f" ({_line(check.summary, 100)})" if check.summary else ""
            evidence.checks.append(f"{check.id} [{check.kind}]: {check.status}{summary}")
            evidence.revision = evidence.revision or (check.revision or "")
        if state.baseline is not None:
            evidence.check_ids.update(c.id for c in state.baseline.checks)
        evidence.repairs = [_line(line, 240) for line in state.verification.repair_log]
    for event in read_events(run_dir / EVENTS_FILENAME):
        if event.type == "check.finished" and event.data.get("status") != "skipped":
            ran = str(event.data.get("check", ""))
            evidence.check_ids.add(ran)
            summary = event.data.get("summary") or ""
            evidence.history.append(
                f"{event.stage or '-'} / {ran}: {event.data.get('status')}"
                + (f" ({_line(summary, 100)})" if summary else "")
            )
    evidence.history = evidence.history[-MAX_HISTORY:]
    ladder = LadderRecord.load(run_dir)
    if ladder is not None:
        evidence.escalations = [
            f"{r.rung}: {r.status}; why: {_line(r.why, 120)}"
            + (f"; failing: {', '.join(r.failing_checks)}" if r.failing_checks else "")
            for r in ladder.rungs
        ]
    entries = list(read_trajectory(ctx.trajectory.path))
    evidence.seqs = {entry.seq for entry in entries}
    evidence.trace = trace_summary(entries, ctx.settings.learning.trace_chars)
    evidence.guidance = _guidance(ctx, {e.teammate for e in entries if e.teammate})
    evidence.skills_loaded = list(ctx.harness.record().get("skills_loaded", []))
    return evidence
