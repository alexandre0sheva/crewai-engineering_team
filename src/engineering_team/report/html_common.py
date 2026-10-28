"""Building blocks of the HTML report: escaping, badges, sections, and tables.

Every string that did not come from this module goes through ``esc``.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

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
