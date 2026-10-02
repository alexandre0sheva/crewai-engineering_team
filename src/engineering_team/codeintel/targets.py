"""Turning what an agent typed (a path, a dotted module name, a file name) into an indexed file."""

from __future__ import annotations

import difflib
from pathlib import PurePosixPath

from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.languages import SUFFIX_LANGUAGES
from engineering_team.tools.support import ToolError


def _candidates(text: str) -> list[str]:
    dotted = text.replace(".", "/")
    return [
        text,
        *(f"{dotted}{suffix}" for suffix in SUFFIX_LANGUAGES),
        f"{dotted}/__init__.py",
        f"{dotted}/index.ts",
        f"{dotted}/index.js",
    ]


def find_file(index: SourceIndex, target: str) -> str | None:
    """The indexed file ``target`` names: an exact path, a dotted module, or a unique file tail."""

    text = PurePosixPath(target.strip().strip("/")).as_posix().removeprefix("./")
    for candidate in _candidates(text):
        if candidate in index.files:
            return candidate
    for candidate in _candidates(text):
        tails = [path for path in index.files if path.endswith("/" + candidate)]
        if len(tails) == 1:
            return tails[0]
    return None


def require_file(index: SourceIndex, target: str) -> str:
    """Like :func:`find_file`, but a miss is a :class:`ToolError` that names close matches."""

    found = find_file(index, target)
    if found is not None:
        return found
    close = difflib.get_close_matches(target.strip(), list(index.files), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else " Use Find Files to locate it."
    raise ToolError(
        f"No indexed source file matches '{target}' (only Python, JS/TS, Go, Java/Kotlin, C#, "
        f"Rust, Ruby, and PHP files are read).{hint}"
    )


def file_or_symbol(index: SourceIndex, target: str) -> str:
    """The indexed file a path-like ``target`` names, otherwise ``target`` itself (a symbol)."""

    text = target.strip()
    if "/" in text or "." in text:
        return find_file(index, text) or text
    return text
