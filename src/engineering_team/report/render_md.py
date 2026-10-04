"""The Markdown report: the same content as the HTML one, for terminals, PRs, and chat.

No timeline drawing, screenshots are links, and the diff is a file table (the patch itself is
``changes.patch`` in the run directory). Text from agents and the repository is neutralised:
cells are one line with ``|`` and ``<`` escaped, logs go in fences longer than any backtick run.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from engineering_team.report.fmt import clock, money, size, tail, time_of_day, when
from engineering_team.report.model import RunReport

SYMBOL = {"good": "✅", "warn": "⚠️", "bad": "❌", "info": "ℹ️"}
MAX_CARD_MOVES = 12


def cell(text: object) -> str:
    """Text for a table cell or list item: one line, no markup that could render."""

    one = re.sub(r"\s+", " ", str(text)).strip()
    return one.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")


def fence(text: str, info: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{text.rstrip()}\n{ticks}"


def table(head: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]
    return lines


def render_markdown(report: RunReport) -> str:
    banner = report.banner
    lines = [f"# Run {cell(report.run_id)}: {cell(banner.label)}", ""]
    lines += [f"{SYMBOL[banner.tone]} **{cell(banner.label)}**", ""]
    lines += [f"- {cell(reason)}" for reason in banner.reasons]
    lines += _summary(report)
    if report.warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {SYMBOL[n.tone]} {cell(n.text)}" for n in report.warnings]
    lines += _timeline(report)
    lines += _board(report)
    lines += _agents(report)
    lines += _screenshots(report)
    lines += _usage(report)
    lines += _checks(report)
    lines += _criteria(report)
    lines += _findings(report)
    lines += _diff(report)
    lines += ["", "## Environment", ""]
    lines += [f"- {cell(key)}: {cell(value)}" for key, value in report.environment]
    lines += [
        "",
        "_Written by the controller from the run directory. Text written by agents or taken from "
        "the repository is not evidence._",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _summary(report: RunReport) -> list[str]:
    usage = report.usage
    facts = [
        ("Project", report.project),
        ("Mode · strategy", f"{report.mode} · {report.strategy}"),
        ("Status", report.status + (f" · {report.verdict}" if report.verdict else "")),
        ("Duration", clock(report.duration_seconds)),
        ("Started", when(report.created)),
        ("Tool calls", report.tool_calls),
    ]
    if usage is not None:
        facts += [("Tokens", f"{usage.totals.total_tokens:,}")]
        facts += [("Estimated cost", money(usage.estimated_cost_usd))]
    lines = ["", "## Summary", ""] + [f"- {key}: {cell(value)}" for key, value in facts]
    if report.questions:
        lines += ["", "Questions for you:", ""]
        lines += [f"{i}. {cell(q)}" for i, q in enumerate(report.questions, 1)]
    if report.request:
        lines += [
            "",
            "<details><summary>Request</summary>",
            "",
            fence(report.request),
            "",
            "</details>",
        ]
    return lines


def _timeline(report: RunReport) -> list[str]:
    if not report.stages:
        return ["", "## Stage timeline", "", "No stages ran."]
    lines = ["", "## Stage timeline", ""]
    rows = [
        (
            s.name,
            s.status,
            s.attempts,
            "-" if s.start is None else f"{s.start:.1f}s",
            "-" if s.end is None else f"{s.end:.1f}s",
            s.detail,
        )
        for s in report.stages
    ]
    lines += table(["Stage", "Status", "Attempts", "Start", "End", "Detail"], rows)
    if report.lanes:
        lines += ["", "Parallel lanes:", ""]
        lines += table(
            ["Stage", "Lane", "Unit", "Status", "Start", "End", "Error"],
            [
                (b.stage, b.lane, b.unit, b.status, f"{b.start:.1f}s", f"{b.end:.1f}s", b.error)
                for b in report.lanes
            ],
        )
    return lines


def _board(report: RunReport) -> list[str]:
    work = [c for c in report.cards if c.kind != "user_note"]
    lines = ["", "## Final task board", ""]
    if not work:
        return lines + ["The board is empty (only the pipeline strategy fills it in)."]
    progress = report.progress
    if progress is not None:
        lines += [
            f"{progress.overall_percent:g}% complete · {progress.cards_done} of "
            f"{progress.cards_total} cards done",
            "",
        ]
    lines += table(
        ["ID", "Title", "Kind", "Assignee", "Status", "Attempts", "Tokens"],
        [(c.id, c.title, c.kind, c.assignee or "-", c.status, c.attempts, c.tokens) for c in work],
    )
    lines += ["", "### Card history", ""]
    for card in work:
        lines.append(f"- **{cell(card.id)}** {cell(card.title)}")
        for move in card.history[:MAX_CARD_MOVES]:
            note = f": {cell(move.note)}" if move.note else ""
            lines.append(
                f"  - {time_of_day(move.ts)} {cell(move.actor)}: "
                f"{cell(move.from_status or '(created)')} → {cell(move.to_status)}{note}"
            )
        if len(card.history) > MAX_CARD_MOVES:
            lines.append(f"  - … {len(card.history) - MAX_CARD_MOVES} more move(s)")
    return lines


def _agents(report: RunReport) -> list[str]:
    if not report.agents:
        return []
    rows = [
        (
            a.agent,
            a.tool_calls,
            a.failed_calls,
            clock(a.seconds),
            ", ".join(a.cards) or "-",
            ", ".join(f"{k} ×{v}" for k, v in list(a.tools.items())[:5]) or "-",
        )
        for a in report.agents
    ]
    return [
        "",
        "## Teammates and tool calls",
        "",
        *table(["Teammate", "Tool calls", "Failed", "Tool time", "Cards", "Most used"], rows),
    ]


def _screenshots(report: RunReport) -> list[str]:
    if not report.screenshots:
        return []
    lines = ["", "## Screenshots", ""]
    for shot in report.screenshots:
        who = f" ({cell(shot.agent)})" if shot.agent else ""
        lines.append(f"- {cell(shot.path)} · {size(shot.size)}{who} {cell(shot.url)}".rstrip())
    return lines


def _usage(report: RunReport) -> list[str]:
    data = report.usage
    if data is None:
        return []
    totals = data.totals
    rows: list[tuple[object, object]] = [
        ("Total tokens", f"{totals.total_tokens:,}"),
        ("Prompt (cached)", f"{totals.prompt_tokens:,} ({totals.cached_prompt_tokens:,})"),
        ("Completion", f"{totals.completion_tokens:,}"),
        ("Model calls", totals.calls),
        ("Tool calls", report.tool_calls),
        ("Estimated cost", money(data.estimated_cost_usd)),
    ]
    lines = ["", "## Usage and cost", "", *table(["Measure", "Value"], rows)]
    if report.budget is not None and report.budget.limits:
        lines += ["", f"Budget: {cell(report.budget.state)}", ""]
        lines += table(
            ["Limit", "Used", "Max", "Fraction"],
            [
                (i.name, f"{i.used:g}", f"{i.max:g}", f"{i.fraction:.0%}")
                for i in report.budget.limits
            ],
        )
    if data.by_stage:
        lines += ["", "By stage:", ""]
        lines += table(
            ["Stage", "Tokens", "Model calls"],
            [(n, f"{t.total_tokens:,}", t.calls) for n, t in data.by_stage.items()],
        )
    return lines


def _checks(report: RunReport) -> list[str]:
    lines = ["", "## Independent checks", ""]
    if not report.checks:
        return lines + ["The controller ran no checks (only the pipeline strategy does)."]
    lines += table(
        ["Check", "Kind", "Required", "Result", "Exit", "Summary"],
        [
            (c.name or c.id, c.kind, "yes" if c.required else "no", c.status,
             "" if c.exit_code is None else c.exit_code, c.summary)
            for c in report.checks
        ],
    )  # fmt: skip
    for c in report.checks:
        if not (c.log_tail or c.hint or c.new_failures):
            continue
        lines += ["", f"### {cell(c.name or c.id)}: {c.status}", ""]
        if c.hint:
            lines += [cell(c.hint), ""]
        lines += [f"- NEW failure: {cell(item)}" for item in c.new_failures]
        if c.log_tail:
            lines.append(fence(tail(c.log_tail, log_path=c.log_path)))
    return lines


def _criteria(report: RunReport) -> list[str]:
    if not report.coverage:
        return []
    lines = ["", "## Criteria coverage", ""]
    rows = [
        (
            c.id,
            c.text,
            c.status if c.status == "verified" else f"{c.status} ⚠ not proven",
            ", ".join(c.checks) or c.note or "-",
        )
        for c in report.coverage
    ]
    return lines + table(["ID", "Criterion", "Status", "Checks / note"], rows)


def _findings(report: RunReport) -> list[str]:
    if not report.findings:
        return []
    rows = [
        (
            f.id or "-",
            f.severity,
            f.summary,
            f"{f.file}:{f.line}" if f.file and f.line else f.file or "",
        )
        for f in report.findings
    ]
    return ["", "## Findings", "", *table(["ID", "Severity", "Summary", "Where"], rows)]


def _diff(report: RunReport) -> list[str]:
    lines = ["", "## Changes", ""]
    view = report.diff
    if view is None:
        return lines + [cell(report.diff_note)]
    lines += [
        f"{len(view.files)} file(s), +{view.added} −{view.removed}. The patch is "
        f"`{view.patch_name}` in the run directory.",
        "",
    ]
    return lines + table(
        ["File", "Status", "Added", "Removed"],
        [(f.path, f.status, f"+{f.added}", f"−{f.removed}") for f in view.files],
    )
