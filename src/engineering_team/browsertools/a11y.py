"""Accessibility Check: heuristics over the ARIA snapshot and a few page facts.

This finds the common, mechanical problems (controls with no accessible name, images without
alternative text, skipped heading levels, vague link text, missing page language or title, text
that is probably too faint). It is a heuristic, **not** a conformance audit: it cannot judge
focus order, keyboard traps, colour in gradients or images, or whether a name is *good*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_FINDINGS = 40
NAME_REQUIRED = {
    "button": ("button-name", "has no accessible name (add visible text or aria-label)"),
    "link": ("link-name", "has no accessible name (add link text or aria-label)"),
    "menuitem": ("button-name", "has no accessible name (add visible text or aria-label)"),
    "tab": ("button-name", "has no accessible name (add visible text or aria-label)"),
}
FIELD_ROLES = frozenset(
    {"textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton"}
    | {"listbox", "menuitemcheckbox", "menuitemradio"}
)
VAGUE_LINKS = frozenset({"click here", "here", "read more", "more", "link", "this", "learn more"})
_LINE = re.compile(r'^(\s*)- ([A-Za-z][\w-]*)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)')
_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class AxNode:
    role: str
    name: str
    level: int | None
    ref: str
    depth: int


@dataclass(frozen=True)
class ContrastIssue:
    text: str
    ratio: float
    required: float
    foreground: str
    background: str


@dataclass
class DomFacts:
    lang: str = ""
    title: str = ""
    contrast: list[ContrastIssue] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    severity: str  # error, warning, info
    rule: str
    message: str
    ref: str = ""


def parse_snapshot(text: str) -> list[AxNode]:
    """Element lines of an ``aria_snapshot(mode="ai")`` (``/url:`` and ``text:`` rows skipped)."""

    nodes: list[AxNode] = []
    for line in text.splitlines():
        match = _LINE.match(line)
        if match is None or match.group(2) in ("text", "/url", "/placeholder"):
            continue
        attributes = match.group(4) or ""
        ref = re.search(r"ref=(\S+?)[\]\s]", attributes + " ")
        level = re.search(r"level=(\d+)", attributes)
        nodes.append(
            AxNode(
                match.group(2),
                (match.group(3) or "").replace('\\"', '"'),
                int(level.group(1)) if level else None,
                ref.group(1) if ref else "",
                len(match.group(1)) // 2,
            )
        )
    return nodes


def check(nodes: list[AxNode], facts: DomFacts) -> list[Finding]:
    found: list[Finding] = []
    for node in nodes:
        if node.role in NAME_REQUIRED and not node.name.strip():
            rule, hint = NAME_REQUIRED[node.role]
            found.append(Finding("error", rule, f"{node.role} {hint}", node.ref))
        elif node.role in FIELD_ROLES and not node.name.strip():
            found.append(
                Finding(
                    "error",
                    "field-label",
                    f"{node.role} has no label (add <label for>, aria-label, or aria-labelledby)",
                    node.ref,
                )
            )
        elif node.role == "img" and not node.name.strip():
            found.append(
                Finding(
                    "warning",
                    "image-alt",
                    'image has no alt text (use alt="" if it is purely decorative)',
                    node.ref,
                )
            )
        if node.role == "link" and node.name.strip().lower() in VAGUE_LINKS:
            found.append(
                Finding(
                    "info",
                    "link-text",
                    f'link text "{node.name}" does not say where it goes',
                    node.ref,
                )
            )
    found.extend(_headings(nodes))
    if not facts.lang.strip():
        found.append(Finding("warning", "html-lang", "<html> has no lang attribute"))
    if not facts.title.strip():
        found.append(Finding("warning", "page-title", "the page has no <title>"))
    for issue in facts.contrast:
        found.append(
            Finding(
                "warning",
                "contrast",
                f'text "{issue.text}" has contrast {issue.ratio:g}:1 '
                f"({issue.foreground} on {issue.background}); {issue.required:g}:1 is needed",
            )
        )
    found.sort(key=lambda f: _ORDER[f.severity])
    return found


def _headings(nodes: list[AxNode]) -> list[Finding]:
    headings = [n for n in nodes if n.role == "heading" and n.level]
    found: list[Finding] = []
    levels = [n for n in headings if n.level == 1]
    if headings and not levels:
        found.append(
            Finding("warning", "h1-missing", "the page has headings but no level-1 heading")
        )
    if len(levels) > 1:
        found.append(
            Finding(
                "warning", "h1-multiple", f"{len(levels)} level-1 headings; use one", levels[1].ref
            )
        )
    previous = 0
    for node in headings:
        assert node.level is not None
        if previous and node.level > previous + 1:
            found.append(
                Finding(
                    "warning",
                    "heading-order",
                    f'heading "{node.name}" jumps from level {previous} to {node.level}',
                    node.ref,
                )
            )
        previous = node.level
    return found


def format_report(findings: list[Finding], *, nodes: int) -> str:
    footer = (
        "These are heuristics over the page's accessibility tree, not a full audit. Run an axe or "
        "Lighthouse audit before claiming conformance."
    )
    if not findings:
        title = "Accessibility check (heuristic, not a full audit)"
        return f"{title}: No problems found in {nodes} elements.\n{footer}"
    counts = {s: sum(1 for f in findings if f.severity == s) for s in _ORDER}
    summary = ", ".join(
        f"{counts[s]} {s}{'s' if counts[s] != 1 else ''}" for s in _ORDER if counts[s]
    )
    lines = [f"Accessibility check (heuristic, not a full audit): {summary} in {nodes} elements"]
    for finding in findings[:MAX_FINDINGS]:
        ref = f" [{finding.ref}]" if finding.ref else ""
        lines.append(f"{finding.severity.upper()} {finding.rule}{ref}: {finding.message}")
    if len(findings) > MAX_FINDINGS:
        lines.append(f"... {len(findings) - MAX_FINDINGS} more")
    lines.append(footer)
    return "\n".join(lines)
