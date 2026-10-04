"""The self-contained HTML report: inline CSS and JavaScript, light and dark, no network."""

from __future__ import annotations

from pathlib import Path

from engineering_team.report import html_sections as s
from engineering_team.report.html_style import CSP, CSS, JS
from engineering_team.report.model import RunReport

NAV = (
    ("summary", "Summary"), ("warnings", "Warnings"), ("timeline", "Timeline"),
    ("board", "Board"), ("agents", "Teammates"), ("screenshots", "Screenshots"),
    ("usage", "Cost"), ("checks", "Checks"), ("criteria", "Criteria"),
    ("findings", "Findings"), ("diff", "Changes"), ("environment", "Environment"),
)  # fmt: skip


def render_html(report: RunReport, run_dir: Path | None = None) -> str:
    """The report page. ``run_dir`` lets it embed the run's screenshots (without it they are
    only listed)."""

    parts = {
        "summary": s.summary(report),
        "warnings": s.warnings(report),
        "timeline": s.timeline(report),
        "board": s.board(report),
        "agents": s.agents(report),
        "screenshots": s.screenshots(report, run_dir),
        "usage": s.usage(report),
        "checks": s.checks(report),
        "criteria": s.criteria(report),
        "findings": s.findings(report),
        "diff": s.diff(report),
        "environment": s.environment(report),
    }
    links = " ".join(f'<a href="#{key}">{label}</a>' for key, label in NAV if parts[key])
    title = f"Run {report.run_id}: {report.banner.label}"
    version = dict(report.environment).get("engineering_team", "")
    footer = (
        '<p class="muted">Written by the controller from the run directory'
        f"{' (engineering-team ' + s.esc(version) + ')' if version else ''}. Text written by "
        "agents or taken from the repository is shown escaped and is not evidence.</p>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{s.esc(title)}</title><style>{CSS}</style></head><body><main>"
        f'<header class="top"><h1>{s.esc(title)}</h1>'
        '<button id="theme" type="button">Theme: auto</button></header>'
        f"{s.banner(report)}"
        f'<nav class="toc">{links}</nav>'
        f"{''.join(parts[key] for key, _ in NAV)}{footer}"
        f"</main><script>{JS}</script></body></html>\n"
    )
