"""The HTML report's sections. Every string that did not come from this module goes through
:func:`esc`: check logs, card titles, agent notes, file names, and diff lines are untrusted."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from html import escape
from pathlib import Path

from engineering_team.board.models import COLUMNS, Card
from engineering_team.report.fmt import clock, money, size, tail, time_of_day, when
from engineering_team.report.model import (
    DiffFile,
    DiffView,
    LaneBar,
    RunReport,
    Screenshot,
    StageRow,
)

MAX_EMBED_BYTES = 3 * 1024 * 1024  # screenshots embedded in all
MAX_EMBED_EACH = 1024 * 1024
MAX_COMMENTS = 5
COLUMN_TITLES = {
    "backlog": "Backlog",
    "ready": "Ready",
    "in_progress": "In progress",
    "verifying": "Verifying",
    "blocked": "Blocked",
    "done": "Done",
    "failed": "Failed",
}
STATUS_TONE = {
    "succeeded": "good", "passed": "good", "verified": "good", "done": "good",
    "failed": "bad", "cancelled": "warn", "interrupted": "warn", "skipped": "warn",
    "unavailable": "warn", "referenced": "warn", "unverified": "bad", "blocked": "warn",
    "running": "info", "pending": "info",
}  # fmt: skip


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def badge(text: str, tone: str | None = None) -> str:
    return f'<span class="badge {tone or STATUS_TONE.get(text, "info")}">{esc(text)}</span>'


def section(ident: str, title: str, body: str) -> str:
    return f'<section id="{ident}"><h2>{esc(title)}</h2>\n{body}</section>\n'


def table(head: Sequence[str], rows: Sequence[Sequence[str]], numeric: Sequence[int] = ()) -> str:
    """A table whose cells are already HTML (built with :func:`esc`)."""

    def cell(tag: str, index: int, content: str) -> str:
        return f"<{tag}{' class=num' if index in numeric else ''}>{content}</{tag}>"

    header = "".join(cell("th", i, esc(name)) for i, name in enumerate(head))
    body = "".join(
        "<tr>" + "".join(cell("td", i, content) for i, content in enumerate(row)) + "</tr>"
        for row in rows
    )
    return (
        f'<div class="scroll"><table><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>" if text else ""


def _list(items: Sequence[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>"


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


# -- board, teammates ------------------------------------------------------------------------


def _card(card: Card) -> str:
    facts = [card.kind, card.assignee or "unassigned"]
    if card.attempts > 1:
        facts.append(f"attempt {card.attempts}")
    if card.tokens:
        facts.append(f"{card.tokens:,} tokens")
    notes = ""
    if card.blocked_reason:
        notes += f"<div>Blocked: {esc(card.blocked_reason)}</div>"
    if card.progress_note:
        notes += f'<div class="muted">{esc(card.progress_note)} (agent-reported)</div>'
    return (
        f'<div class="card"><strong>{esc(card.id)}</strong> {esc(card.title)}'
        f'<div class="muted">{esc(" · ".join(facts))}</div>{notes}</div>'
    )


def _evidence(ids: Sequence[str]) -> str:
    return f" — evidence: {esc(', '.join(ids))}" if ids else ""


def _history(card: Card) -> str:
    moves = "".join(
        f"<tr><td>{esc(time_of_day(m.ts))}</td><td>{esc(m.actor)}</td>"
        f"<td>{esc(m.from_status or '(created)')} → {esc(m.to_status)}</td>"
        f"<td>{esc(m.note)}{_evidence(m.evidence)}</td></tr>"
        for m in card.history
    )
    comments = "".join(
        f"<li>{esc(c.author)}: {esc(c.text)}</li>" for c in card.comments[:MAX_COMMENTS]
    )
    more = len(card.comments) - MAX_COMMENTS
    extra = f"<li>… {more} more comment(s)</li>" if more > 0 else ""
    notes = f"<ul>{comments}{extra}</ul>" if comments else ""
    body = (
        "<table><thead><tr><th>Time (UTC)</th><th>Who</th><th>Move</th><th>Note</th></tr></thead>"
        f"<tbody>{moves}</tbody></table>{notes}"
    )
    label = f"{esc(card.id)} {esc(card.title)} · {len(card.history)} move(s) {badge(card.status)}"
    return f"<details><summary>{label}</summary>{body}</details>"


def board(report: RunReport) -> str:
    work = [c for c in report.cards if c.kind != "user_note"]
    if not work:
        return section(
            "board",
            "Task board",
            '<p class="muted">The board is empty (only the pipeline strategy fills it in).</p>',
        )
    progress = report.progress
    head = ""
    if progress is not None:
        head = (
            f"<p>{progress.overall_percent:g}% complete · {progress.cards_done} of "
            f"{progress.cards_total} cards done{' · PAUSED' if report.paused else ''}</p>"
        )
    columns = ""
    for status in (*COLUMNS, "cancelled"):
        cards = [c for c in work if c.status == status]
        if not cards and status == "cancelled":
            continue
        title = COLUMN_TITLES.get(status, status.capitalize())
        columns += (
            f'<div class="col"><h3>{esc(title)} ({len(cards)})</h3>'
            f"{''.join(_card(c) for c in cards) or '<span class=muted>none</span>'}</div>"
        )
    history = "".join(_history(c) for c in work)
    return section(
        "board",
        "Final task board",
        f'{head}<div class="columns">{columns}</div><h3>Card history</h3>{history}',
    )


def agents(report: RunReport) -> str:
    if not report.agents:
        return ""
    rows = [
        [
            esc(a.agent),
            str(a.tool_calls),
            str(a.failed_calls),
            esc(clock(a.seconds)),
            esc(", ".join(a.cards) or "-"),
            esc(", ".join(f"{name} ×{count}" for name, count in list(a.tools.items())[:5]) or "-"),
        ]
        for a in report.agents
    ]
    return section(
        "agents",
        "Teammates and tool calls",
        table(
            ["Teammate", "Tool calls", "Failed", "Tool time", "Cards", "Most used"], rows, (1, 2)
        ),
    )


# -- screenshots -----------------------------------------------------------------------------


def _embedded(run_dir: Path, shot: Screenshot, spent: int) -> str | None:
    """The screenshot as a ``data:`` URI, or ``None`` (too big, over budget, not a real file)."""

    folder = (run_dir / "screenshots").resolve()
    path = run_dir / shot.path
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
            return None
        if path.stat().st_size > MAX_EMBED_EACH or spent + path.stat().st_size > MAX_EMBED_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def screenshots(report: RunReport, run_dir: Path | None) -> str:
    if not report.screenshots:
        return ""
    spent = 0
    figures: list[str] = []
    for shot in report.screenshots:
        uri = _embedded(run_dir, shot, spent) if run_dir is not None else None
        caption = esc(shot.name) + (f" · {esc(shot.agent)}" if shot.agent else "")
        caption += f" · {esc(shot.url)}" if shot.url else ""
        if uri is None:
            figures.append(
                f'<figure><figcaption>{caption} · <span class="muted">not embedded '
                f"({esc(size(shot.size))}); see {esc(shot.path)} in the run directory</span>"
                "</figcaption></figure>"
            )
            continue
        spent += len(uri) * 3 // 4
        figures.append(
            f'<figure><img src="{uri}" alt="{esc(shot.name)}" loading="lazy">'
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return section("screenshots", "Screenshots", "".join(figures))


# -- usage, checks, criteria, findings -------------------------------------------------------


def usage(report: RunReport) -> str:
    data = report.usage
    if data is None:
        return ""
    totals = data.totals
    rows = [
        ["Total tokens", f"{totals.total_tokens:,}"],
        ["Prompt (of which cached)", f"{totals.prompt_tokens:,} ({totals.cached_prompt_tokens:,})"],
        ["Completion", f"{totals.completion_tokens:,}"],
        ["Model calls", f"{totals.calls:,}"],
        ["Tool calls", f"{report.tool_calls:,}"],
        ["Estimated cost", money(data.estimated_cost_usd)],
    ]
    if data.estimated_cost_usd is None and data.known_cost_usd:
        rows.append(["Priced part of the cost", money(data.known_cost_usd)])
    body = table(["Measure", "Value"], [[esc(name), esc(value)] for name, value in rows], (1,))
    if report.budget is not None and report.budget.limits:
        limits = []
        for item in report.budget.limits:
            tone = "bad" if item.fraction >= 1 else "warn" if item.fraction >= 0.8 else ""
            cost = item.name.endswith("cost_usd")
            used, cap = (
                (money(item.used), money(item.max)) if cost else (f"{item.used:g}", f"{item.max:g}")
            )
            width = min(100.0, item.fraction * 100)
            meter = f'<div class="meter"><i class="{tone}" style="width:{width:.0f}%"></i></div>'
            limits.append([esc(item.name), esc(used), esc(cap), f"{item.fraction:.0%}", meter])
        body += f"<h3>Budget ({esc(report.budget.state)})</h3>" + table(
            ["Limit", "Used", "Max", "Fraction", ""], limits, (1, 2, 3)
        )
    else:
        body += '<p class="muted">No budget was set.</p>'
    if data.by_stage:
        by_stage = [
            [esc(name), f"{t.total_tokens:,}", str(t.calls)] for name, t in data.by_stage.items()
        ]
        body += "<h3>By stage</h3>" + table(["Stage", "Tokens", "Model calls"], by_stage, (1, 2))
    if data.by_agent:
        by_agent = [
            [esc(name), f"{t.total_tokens:,}", str(t.calls)] for name, t in data.by_agent.items()
        ]
        body += "<h3>By teammate</h3>" + table(
            ["Teammate", "Tokens", "Model calls"], by_agent, (1, 2)
        )
    if data.cost_by_model:
        by_model = [[esc(m), esc(money(c))] for m, c in data.cost_by_model.items()]
        body += "<h3>By model</h3>" + table(["Model", "Estimated cost"], by_model, (1,))
    return section("usage", "Usage and cost", body)


def checks(report: RunReport) -> str:
    if not report.checks:
        return section(
            "checks",
            "Independent checks",
            '<p class="muted">The controller ran no checks (only the pipeline strategy does).</p>',
        )
    rows = [
        [
            esc(c.name or c.id),
            esc(c.kind),
            "yes" if c.required else "no",
            badge(c.status),
            "" if c.exit_code is None else str(c.exit_code),
            esc(f"{c.duration:.1f}s"),
            esc(c.summary),
            code(c.command),
        ]
        for c in report.checks
    ]
    body = table(
        ["Check", "Kind", "Required", "Result", "Exit", "Time", "Summary", "Command"], rows, (4, 5)
    )
    for c in report.checks:
        extra = ""
        if c.hint:
            extra += f"<p>{esc(c.hint)}</p>"
        if c.suspect_files:
            extra += f"<p>Files it points at: {esc(', '.join(c.suspect_files))}</p>"
        if c.new_failures or c.known_failures:
            extra += "<ul>" + "".join(f"<li>NEW: {esc(k)}</li>" for k in c.new_failures)
            extra += "".join(f"<li>known (baseline): {esc(k)}</li>" for k in c.known_failures)
            extra += "</ul>"
        if c.log_tail:
            extra += f"<pre>{esc(tail(c.log_tail, log_path=c.log_path))}</pre>"
        if not extra:
            continue
        open_ = " open" if c.status != "passed" else ""
        body += (
            f"<details{open_}><summary>{esc(c.name or c.id)} {badge(c.status)} — log excerpt"
            f"</summary>{extra}</details>"
        )
    return section("checks", "Independent checks", body)


def criteria(report: RunReport) -> str:
    if not report.coverage:
        return ""
    rows = []
    for item in report.coverage:
        proof = ", ".join(item.checks) if item.checks else ""
        flag = " ⚠ not proven" if item.status != "verified" else ""
        rows.append(
            [
                esc(item.id),
                esc(item.text),
                badge(item.status) + esc(flag),
                esc(proof or "-"),
                esc(item.note),
            ]
        )
    unverified = sum(1 for item in report.coverage if item.status != "verified")
    lead = (
        f"<p>{unverified} of {len(report.coverage)} criteria are <strong>not</strong> proven by a "
        "passing check; a person has to confirm them.</p>"
        if unverified
        else "<p>Every criterion is proven by a passing mapped check.</p>"
    )
    return section(
        "criteria",
        "Criteria coverage",
        lead + table(["ID", "Criterion", "Status", "Checks", "Note"], rows),
    )


def findings(report: RunReport) -> str:
    if not report.findings:
        return ""
    tone = {"critical": "bad", "high": "bad", "medium": "warn", "low": "info", "info": "info"}
    rows = [
        [
            esc(f.id or "-"),
            badge(f.severity, tone.get(f.severity, "info")),
            esc(f.summary),
            code(f"{f.file}:{f.line}" if f.file and f.line else f.file or ""),
            esc(f.suggested_fix or ""),
            esc(f.source_role or ""),
        ]
        for f in report.findings
    ]
    return section(
        "findings",
        "Findings",
        table(["ID", "Severity", "Summary", "Where", "Suggested fix", "Found by"], rows),
    )


# -- diff, environment -----------------------------------------------------------------------


def _diff_file(item: DiffFile, open_: bool) -> str:
    lines = []
    for line in item.lines:
        kind = (
            "add"
            if line.startswith("+")
            else "del"
            if line.startswith("-")
            else "hunk"
            if line.startswith("@@")
            else ""
        )
        lines.append(f'<span class="{kind}">{esc(line) or "&nbsp;"}</span>')
    more = (
        f'<span class="hunk">… {item.omitted} more line(s) not shown</span>' if item.omitted else ""
    )
    note = "binary file" if item.binary else ""
    body = (
        f'<pre class="diff">{"".join(lines)}{more}</pre>'
        if lines or more
        else f"<p class=muted>{note or 'no content change'}</p>"
    )
    label = f"{esc(item.path)} · {esc(item.status)} · +{item.added} −{item.removed}"
    return f"<details{' open' if open_ else ''}><summary>{label}</summary>{body}</details>"


def diff(report: RunReport) -> str:
    view: DiffView | None = report.diff
    if view is None:
        return section("diff", "Changes", f'<p class="muted">{esc(report.diff_note)}</p>')
    stats = table(
        ["File", "Status", "Added", "Removed"],
        [[code(f.path), esc(f.status), f"+{f.added}", f"−{f.removed}"] for f in view.files],
        (2, 3),
    )
    shown = "".join(_diff_file(f, len(view.files) <= 3) for f in view.files)
    note = (
        f'<p class="muted">Large diff: lines beyond the limits are not shown; the whole change is '
        f"{esc(view.patch_name)} in the run directory.</p>"
        if view.truncated
        else ""
    )
    lead = f"<p>{len(view.files)} file(s), +{view.added} −{view.removed}.</p>"
    return section("diff", "Changes", f"{lead}{stats}<h3>Diff</h3>{note}{shown}")


def environment(report: RunReport) -> str:
    rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in report.environment)
    return section("environment", "Environment", f'<dl class="facts">{rows}</dl>')
