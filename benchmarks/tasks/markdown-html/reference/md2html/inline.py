"""Inline formatting."""

import html
import re


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def render(text: str) -> str:
    out = []
    for index, part in enumerate(re.split(r"(`[^`]+`)", text)):
        if index % 2:
            out.append(f"<code>{escape(part[1:-1])}</code>")
            continue
        part = escape(part)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", part)
        part = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<em>\1</em>", part)
        part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', part)
        out.append(part)
    return "".join(out)
