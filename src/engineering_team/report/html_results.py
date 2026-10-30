"""The report's results sections: usage, checks, criteria, and findings.

Every string that did not come from this module goes through ``esc``.
"""

from __future__ import annotations

from engineering_team.report.fmt import money, tail
from engineering_team.report.html_common import badge, code, esc, section, table
from engineering_team.report.model import (
    RunReport,
)

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


def delegations(report: RunReport) -> str:
    """Who the lead called, why, and what it cost (the team strategy)."""

    if not report.delegations:
        return ""
    rows = [
        [
            esc(d.id),
            esc(d.teammate),
            esc(d.kind.replace("_", " ")),
            badge(d.status),
            esc(d.why or "-"),
            esc(", ".join(d.files_changed[:6]) + (" ..." if len(d.files_changed) > 6 else "")),
            str(d.calls),
            esc(money(d.cost_usd)),
        ]
        for d in report.delegations
    ]
    head = ["Job", "Teammate", "Kind", "Status", "Why (the lead)", "Files", "Model calls", "Cost"]
    return section("delegations", "Delegations", table(head, rows, (6, 7)))


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
