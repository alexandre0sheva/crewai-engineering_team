"""Formatters in check mode: which files would change."""

from __future__ import annotations

import re

from engineering_team.devtools.parsers.common import rel_path, strip_ansi

# One pattern per tool; ``gofmt -l`` prints bare file names, so it has none.
CHECK_PATTERNS = {
    "ruff": re.compile(r"^Would reformat: (?P<file>.+)$"),
    "black": re.compile(r"^would reformat (?P<file>.+)$"),
    "prettier": re.compile(r"^\[warn\] (?P<file>(?!Code style issues|Run Prettier)\S.*)$"),
    "rustfmt": re.compile(r"^Diff in (?P<file>.+?)(?::\d+:| at line \d+:)$"),
}
# ruff 0.16+ prints "unformatted: File would be reformatted" and then " --> path:line:col".
RUFF_LOCATION = re.compile(r"^--> (?P<file>.+?):\d+:\d+$")


def parse_format_check(tool: str, text: str, root: str | None = None) -> list[str]:
    """Files a formatter's check mode says need formatting, sorted and without duplicates.

    ``tool`` is ``ruff``, ``black``, ``prettier``, ``gofmt`` (``-l`` lists one file per line) or
    ``rustfmt`` (``cargo fmt --check`` prints diffs).
    """

    files: set[str] = set()
    pattern = CHECK_PATTERNS.get(tool)
    for raw in strip_ansi(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if pattern is not None:
            if (
                (match := pattern.match(line))
                or tool == "ruff"
                and (match := RUFF_LOCATION.match(line))
            ):
                files.add(match["file"])
        elif not line.startswith(("#", "can't", "gofmt:")):
            files.add(line)
    return sorted(rel_path(name, root) for name in files)
