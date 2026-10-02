"""Find Related Tests: the tests that import, are named after, or mention a file or symbol."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.languages import language_of
from engineering_team.codeintel.references import word_pattern

MAX_RELATED = 50
TEST_DIRECTORIES = frozenset({"tests", "test", "__tests__", "spec", "specs"})
_NOT_TESTS = frozenset({"__init__.py", "conftest.py"})
_TEST_NAME = re.compile(
    r"^(?:test_.+\.py|.+_tests?\.(?:py|go|rs|rb)|.+\.(?:test|spec)\.[cm]?[jt]sx?"
    r"|.+(?:Tests?|IT)\.(?:java|kt|cs|php)|.+_spec\.rb)$"
)
_AFFIXES = re.compile(r"^test_|_tests?$|\.(?:test|spec)$|(?:Tests?|IT)$|_spec$")
# Names too generic to prove a test is about the file that defines them.
_GENERIC = frozenset({"main", "init", "setup", "test", "run", "get", "set", "load", "new"})


@dataclass(frozen=True)
class RelatedTest:
    path: str
    reasons: list[str]
    score: int


def is_test_path(path: str) -> bool:
    name = posixpath.basename(path)
    if language_of(path) is None or name in _NOT_TESTS:
        return False
    return bool(_TEST_NAME.match(name)) or any(
        part in TEST_DIRECTORIES for part in path.split("/")[:-1]
    )


def _stem(path: str) -> str:
    return _AFFIXES.sub("", posixpath.splitext(posixpath.basename(path))[0]).lower()


def related_tests(index: SourceIndex, graph: ImportGraph, target: str) -> list[RelatedTest]:
    """Tests related to ``target``: a source file path, or else a symbol name.

    For a file: tests that import it (strongest), tests named after it, and tests that mention
    a top-level name it defines. For a symbol: tests that mention the name.
    """

    tests = {path: source for path, source in index.files.items() if is_test_path(path)}
    scored: dict[str, tuple[int, list[str]]] = {}

    def add(path: str, points: int, reason: str) -> None:
        score, reasons = scored.setdefault(path, (0, []))
        if reason not in reasons:
            scored[path] = (score + points, [*reasons, reason])

    if target in index.files:
        for importer, _ in graph.importers_of(target):
            if importer in tests:
                add(importer, 3, f"imports {target}")
        names = sorted(
            {
                d.name
                for d in index.definitions(target)
                if d.parent is None and len(d.name) >= 4 and d.name.lower() not in _GENERIC
            }
        )
        for path, source in tests.items():
            if path != target and _stem(path) == _stem(target):
                add(path, 1, "name matches")
            if mentioned := [n for n in names if word_pattern(n).search(source.text)]:
                add(path, 2, "mentions " + ", ".join(mentioned[:3]))
    else:
        word = word_pattern(target)
        for path, source in tests.items():
            if word.search(source.text):
                add(path, 2, f"mentions {target}")
    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
    return [RelatedTest(path, reasons, score) for path, (score, reasons) in ranked[:MAX_RELATED]]
