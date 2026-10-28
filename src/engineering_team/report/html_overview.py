"""The report's opening sections: banner, summary, warnings, and the timeline.

Every string that did not come from this module goes through ``esc``.
"""

from __future__ import annotations

from engineering_team.report.fmt import clock, money, when
from engineering_team.report.html_common import _list, badge, esc, section
from engineering_team.report.model import (
    LaneBar,
    RunReport,
    StageRow,
)

# -- summary ---------------------------------------------------------------------------------


def banner(report: RunReport) -> str:
    banner = report.banner
    reasons = _list(banner.reasons) if banner.reasons else ""
    return (
        f'<div class="banner {banner.tone}" role="status"><h2>{esc(banner.label)}</h2>'
        f"{reasons}</div>"
    )


def summary(report: RunReport) -> str:
    usage = report.usage
    progress = report.progress
    facts = [
        ("Run", report.run_id),
        ("Project", report.project),
        ("Mode · strategy", f"{report.mode} · {report.strategy}"),
        ("Status", f"{report.status}" + (f" · {report.verdict}" if report.verdict else "")),
        ("Duration", clock(report.duration_seconds)),
        ("Started", when(report.created)),
        ("Ended", when(report.finished) if report.status not in ("running", "pending") else "-"),
        ("Stages", f"{sum(s.status == 'succeeded' for s in report.stages)}/{len(report.stages)}"),
    ]
    if progress is not None:
        facts.append(("Board", f"{progress.cards_done}/{progress.cards_total} cards done"))
    facts.append(("Tool calls", str(report.tool_calls)))
    if usage is not None:
        facts.append(("Tokens", f"{usage.totals.total_tokens:,}"))
        facts.append(("Estimated cost", money(usage.estimated_cost_usd)))
    if report.diff is not None:
        facts.append(
            (
                "Files changed",
                f"{len(report.diff.files)} (+{report.diff.added} −{report.diff.removed})",
            )
        )
    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in facts)
    request = (
        f"<details><summary>Request</summary><pre>{esc(report.request)}</pre></details>"
        if report.request
        else ""
    )
    questions = ""
    if report.questions:
        questions = (
            "<h3>Questions for you</h3><ol>"
            + "".join(f"<li>{esc(q)}</li>" for q in report.questions)
            + "</ol>"
        )
    return section("summary", "Summary", f'<dl class="facts">{cells}</dl>{request}{questions}')


def warnings(report: RunReport) -> str:
    if not report.warnings:
        return ""
    items = "".join(
        f'<li><span class="badge {n.tone}">{esc(n.tone)}</span> {esc(n.text)}</li>'
        for n in report.warnings
    )
    return section("warnings", "Warnings", f"<ul>{items}</ul>")


# -- timeline --------------------------------------------------------------------------------


def _bar(start: float | None, end: float | None, total: float, status: str, title: str) -> str:
    if start is None or end is None or total <= 0:
        return ""
    left = min(100.0, max(0.0, start / total * 100))
    width = max(0.4, min(100.0 - left, (end - start) / total * 100))
    return (
        f'<span class="tl-bar {esc(status)}" style="left:{left:.2f}%;width:{width:.2f}%" '
        f'title="{esc(title)}"></span>'
    )


def _lane_label(bar: LaneBar) -> str:
    return f"lane {bar.lane} · {bar.unit}"


def timeline(report: RunReport) -> str:
    if not report.stages:
        return section("timeline", "Stage timeline", '<p class="muted">No stages ran.</p>')
    total = report.timeline_seconds
    rows: list[str] = []
    for stage in report.stages:
        rows.append(_timeline_row(stage, total))
        for lane in (b for b in report.lanes if b.stage == stage.name):
            tip = f"{_lane_label(lane)}: {lane.status} ({clock(lane.end - lane.start)})"
            if lane.error:
                tip += f" — {lane.error}"
            label = esc(_lane_label(lane))
            bar = _bar(lane.start, lane.end, total, lane.status, tip)
            rows.append(
                f'<div class="tl-row"><div class="tl-label tl-lane" title="{label}">{label}</div>'
                f'<div class="tl-track">{bar}</div></div>'
            )
    axis = f'<div class="tl-axis"><span>0s</span><span>{esc(clock(total))}</span></div>'
    return section("timeline", "Stage timeline", f'<div class="tl">{"".join(rows)}{axis}</div>')


def _timeline_row(stage: StageRow, total: float) -> str:
    span = (stage.end or 0) - (stage.start or 0)
    tip = f"{stage.name}: {stage.status}, {clock(span)}, attempt {stage.attempts}"
    if stage.detail:
        tip += f" — {stage.detail}"
    bar = _bar(stage.start, stage.end, total, stage.status, tip)
    return (
        f'<div class="tl-row"><div class="tl-label" title="{esc(stage.name)}">'
        f"{esc(stage.name)} {badge(stage.status)}</div>"
        f'<div class="tl-track">{bar}</div></div>'
    )
