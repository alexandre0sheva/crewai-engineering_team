"""Helpers shared by the parsers: bounded text and tidy paths."""

from __future__ import annotations

import json
import re
from typing import Any

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def excerpt(text: str, limit: int, *, lines: int | None = None) -> str:
    """At most ``limit`` characters (and ``lines`` lines), marked when cut."""

    text = text.strip("\n")
    cut = False
    if lines is not None and text.count("\n") + 1 > lines:
        text = "\n".join(text.splitlines()[:lines])
        cut = True
    if len(text) > limit:
        text, cut = text[:limit], True
    return text + "\n..." if cut else text


def rel_path(path: str, root: str | None = None) -> str:
    """``path`` relative to ``root`` when it lies below it; a leading ``./`` is dropped."""

    if root:
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path[2:] if path.startswith("./") else path


def extract_json(text: str) -> Any:
    """The JSON value in ``text``, which may have warnings or progress lines around it.

    Raises :class:`ValueError` when there is none.
    """

    decoder = json.JSONDecoder()
    try:
        return json.loads(text)
    except ValueError:
        pass
    offset = 0
    for line in text.splitlines(keepends=True):
        if line[:1] in ("[", "{"):
            try:
                return decoder.raw_decode(text[offset:])[0]
            except ValueError:
                pass
        offset += len(line)
    raise ValueError("no JSON found in the output")


def json_text(text: str) -> str:
    """The JSON part of a log that also holds warnings, as text for the JSON parsers."""

    try:
        return json.dumps(extract_json(text))
    except ValueError:
        return text
