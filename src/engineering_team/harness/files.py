"""Reading harness component files: UTF-8 Markdown with optional YAML front-matter.

A component file is ``---`` front-matter (YAML mapping) followed by a Markdown body. Paragraphs
are separated by blank lines; the lines of a paragraph join with single spaces, and paragraphs
join with one newline, which is how YAML folded scalars (``>``) read, so the text a model gets
is the text the 0.2.0 YAML gave it. Every problem is collected as one line naming the file and
saying what to fix; :class:`HarnessError` carries them all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from importlib.resources.abc import Traversable
from typing import Any

import yaml

SECTION = re.compile(r"^##\s+(.+?)\s*$")
TITLE = re.compile(r"^#\s+\S")


class HarnessError(ValueError):
    """A harness that cannot be loaded; one line per problem, each naming its file."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def read(path: Traversable, where: str, problems: list[str]) -> str | None:
    """The file's text, or ``None`` (and a problem) when it cannot be read as UTF-8."""

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        problems.append(f"{where}: not UTF-8 text; save it as UTF-8.")
    except OSError as exc:
        problems.append(f"{where}: cannot be read ({exc.strerror or exc}).")
    return None


def split_front_matter(text: str, where: str, problems: list[str]) -> tuple[dict[str, Any], str]:
    """``(front-matter, body)``; a file without front-matter has an empty mapping."""

    text = text.removeprefix("﻿")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end < 0 and text.rstrip().endswith("\n---"):
        end = len(text.rstrip()) - 4
    if end < 0:
        problems.append(
            f"{where}: the front-matter starts with --- but never ends; add a --- line."
        )
        return {}, text
    try:
        data = yaml.safe_load(text[4 : end + 1]) or {}
    except yaml.YAMLError as exc:
        problems.append(f"{where}: the front-matter is not valid YAML ({_short(exc)}).")
        return {}, text[end + 5 :]
    if not isinstance(data, dict):
        problems.append(f"{where}: the front-matter must be a mapping of field: value.")
        return {}, text[end + 5 :]
    return data, text[end + 5 :]


def fold(body: str) -> str:
    """The body as a folded paragraph text: lines joined by spaces, paragraphs by newlines."""

    paragraphs: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip():
            current.append(line.strip())
            continue
        if current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def sections(
    body: str, allowed: tuple[str, ...], where: str, problems: list[str], first_line: int = 1
) -> dict[str, str]:
    """The ``## Heading`` sections of ``body`` by lowercase heading (a ``# title`` line on top is
    allowed and ignored). Text outside a section or an unknown or repeated heading is a problem;
    ``first_line`` is the body's line number in its file."""

    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for number, line in enumerate(body.splitlines(), start=first_line):
        match = SECTION.match(line)
        if match:
            name = match.group(1).strip().lower()
            if name not in allowed:
                problems.append(
                    f"{where}: unknown section '## {match.group(1)}'; use "
                    f"{', '.join('## ' + a.capitalize() for a in allowed)}."
                )
                current = []
            elif name in found:
                problems.append(f"{where}: section '## {match.group(1)}' appears twice.")
                current = []
            else:
                current = found[name] = []
            continue
        if current is None:
            if line.strip() and not (TITLE.match(line) and not found):
                problems.append(
                    f"{where}, line {number}: text before the first ## section; move it into one."
                )
            continue
        current.append(line)
    return {name: fold("\n".join(lines)) for name, lines in found.items()}


def unknown_fields(
    data: dict[str, Any], allowed: Iterable[str], where: str, problems: list[str]
) -> None:
    known = set(allowed)
    for name in data:
        if name not in known:
            problems.append(
                f"{where}: unknown front-matter field '{name}'; known: {', '.join(sorted(known))}."
            )


def _short(exc: yaml.YAMLError) -> str:
    return " ".join(str(exc).split())[:200]
