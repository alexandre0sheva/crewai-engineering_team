"""The HTML report's sections, by what they show; ``render_html`` assembles them.

Every string that did not come from these modules goes through :func:`esc`: check logs, card
titles, agent notes, file names, and diff lines are untrusted. The modules are ``html_common``
(escaping, tables), ``html_overview``, ``html_board``, ``html_results`` and ``html_artifacts``.
"""

from engineering_team.report.html_artifacts import diff, environment, screenshots, trace
from engineering_team.report.html_board import agents, board
from engineering_team.report.html_common import (
    COLUMN_TITLES,
    MAX_COMMENTS,
    MAX_EMBED_BYTES,
    MAX_EMBED_EACH,
    STATUS_TONE,
    badge,
    code,
    esc,
    section,
    table,
)
from engineering_team.report.html_overview import banner, summary, timeline, warnings
from engineering_team.report.html_profile import profile
from engineering_team.report.html_results import (
    checks,
    criteria,
    delegations,
    escalation,
    findings,
    usage,
)

__all__ = [
    "COLUMN_TITLES",
    "MAX_COMMENTS",
    "MAX_EMBED_BYTES",
    "MAX_EMBED_EACH",
    "STATUS_TONE",
    "agents",
    "badge",
    "banner",
    "board",
    "checks",
    "code",
    "criteria",
    "delegations",
    "diff",
    "environment",
    "escalation",
    "esc",
    "findings",
    "profile",
    "screenshots",
    "section",
    "summary",
    "table",
    "timeline",
    "trace",
    "usage",
    "warnings",
]
