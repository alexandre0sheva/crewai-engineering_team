"""Find References: every word-boundary use of a name, classified by how it is used."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engineering_team.codeintel.imports import import_lines
from engineering_team.codeintel.index import SourceFile, SourceIndex

_COMMENT_STARTS = {
    "python": ("#",),
    "ruby": ("#",),
    "php": ("#", "//", "/*", "*"),
}
_C_COMMENT = ("//", "/*", "*")


@dataclass(frozen=True)
class Reference:
    path: str
    line: int
    kind: str  # definition, import, call, other
    text: str


def word_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")


def _is_comment(language: str, line: str) -> bool:
    return line.lstrip().startswith(_COMMENT_STARTS.get(language, _C_COMMENT))


def find_references(index: SourceIndex, name: str, *, path_prefix: str = "") -> list[Reference]:
    """Uses of ``name`` in every source file (case-sensitive), sorted by file and line.

    A *definition* is a line where ``name`` is declared, an *import* a line inside an import
    statement, a *call* a use followed by ``(`` in code, and *other* anything else (type
    annotations, attribute reads, comments, strings).
    """

    word = word_pattern(name)
    call = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    found: list[Reference] = []
    for path, source in index.files.items():
        if path_prefix and not (path == path_prefix or path.startswith(path_prefix + "/")):
            continue
        hits = [
            (number, line)
            for number, line in enumerate(source.text.splitlines(), start=1)
            if word.search(line)
        ]
        if not hits:
            continue
        defined = {d.line for d in index.definitions(path) if d.name == name}
        imported = import_lines(source)
        found.extend(
            Reference(path, number, _classify(source, line, number, defined, imported, call), line)
            for number, line in hits
        )
    return sorted(found, key=lambda ref: (ref.path, ref.line))


def _classify(
    source: SourceFile,
    line: str,
    number: int,
    defined: set[int],
    imported: frozenset[int],
    call: re.Pattern[str],
) -> str:
    if number in defined:
        return "definition"
    if number in imported:
        return "import"
    if not _is_comment(source.language, line) and call.search(line):
        return "call"
    return "other"
