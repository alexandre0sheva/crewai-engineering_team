"""Where a request starts: the files, symbols and words in it that the ranking is seeded with.

The sources are the request text (which may hold a stack trace or a reproduction command), the
triage focus paths and the reproduction's files. Files are found however much of their leading
path differs from the checkout (a trace from another machine), a stack trace's innermost frame
counts most, and only identifiers that read as code (``snake_case``, ``CamelCase``, names in
backticks) seed by symbol, so an English word that happens to be a function name does not.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.languages import SUFFIX_LANGUAGES
from engineering_team.codeintel.lexical import request_terms

FRAME_DECAY = 0.8  # each outer stack frame counts this much of the one inside it...
FRAME_FLOOR = 0.3  # ...but never less than this
MAX_SYMBOL_FILES = 3  # a name defined in more files than this says nothing about the request
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)")
SUFFIXES = tuple(SUFFIX_LANGUAGES)
PATH_TOKEN = re.compile(r"[\w.\\/-]*\w\.(?:" + "|".join(s[1:] for s in SUFFIXES) + r")\b")


@dataclass(frozen=True)
class Seeds:
    """Where the work starts: files with a weight, symbol names, and words from the request."""

    paths: Mapping[str, float] = field(default_factory=dict)
    symbols: frozenset[str] = frozenset()
    terms: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.paths or self.symbols or self.terms)


def resolve_path(index: SourceIndex, token: str) -> str | None:
    """The indexed file a path-like ``token`` names, however much of its leading path differs
    (a trace from another checkout: ``/home/sam/src/app/pkg/mod.py`` finds ``pkg/mod.py``)."""

    parts = [part for part in token.strip().replace("\\", "/").split("/") if part not in ("", ".")]
    for start in range(len(parts)):
        candidate = "/".join(parts[start:])
        if candidate in index.files:
            return candidate
    if parts:
        tails = [path for path in index.files if path.endswith("/" + parts[-1])]
        if len(tails) == 1:
            return tails[0]
    return None


def frames(text: str) -> list[str]:
    """File-like tokens of ``text`` in order of importance: a stack trace innermost frame first,
    anything else as written."""

    from engineering_team.modes.trace import parse_trace

    trace = parse_trace(text)
    found = [frame.file for frame in trace.frames] if trace is not None else []
    return [*found, *(match.group(0) for match in PATH_TOKEN.finditer(text))]


def seeds_from(
    index: SourceIndex,
    text: str,
    *,
    focus_paths: Iterable[str] = (),
    files: Iterable[str] = (),
) -> Seeds:
    """The seeds of a request: ``text`` (the request, a trace, a reproduction command),
    ``focus_paths`` (triage's, files or folders) and ``files`` (the reproduction's)."""

    paths: dict[str, float] = {}

    def add(path: str, weight: float) -> None:
        paths[path] = max(paths.get(path, 0.0), weight)

    for focus in focus_paths:
        folder = focus.strip().replace("\\", "/").strip("/")
        inside = [p for p in index.files if folder and p.startswith(folder + "/")]
        found = resolve_path(index, focus)
        for path in [found] if found else inside:
            add(path, 1.0 if found else 0.5)
    for path in files:
        if (found := resolve_path(index, path)) is not None:
            add(found, 1.0)
    weight = 1.0
    for token in frames(text):
        found = resolve_path(index, token)
        if found is not None and found not in paths:
            add(found, weight)
            weight = max(FRAME_FLOOR, weight * FRAME_DECAY)
    defined: dict[str, set[str]] = defaultdict(set)
    for definition in index.all_definitions():
        defined[definition.name].add(definition.path)
    names = {
        name
        for name in code_names(text)
        if name in defined and len(defined[name]) <= MAX_SYMBOL_FILES
    }
    return Seeds(paths, frozenset(names), request_terms(text, len(index.files)))


def code_names(text: str) -> set[str]:
    """Identifiers that read as code, not as English: ``snake_case``, ``CamelCase``, names in
    backticks and the last part of a dotted one (``obj.method``)."""

    names = {
        name
        for name in IDENTIFIER.findall(text)
        if "_" in name.strip("_") or name != name.lower() and name != name.capitalize()
    }
    for dotted in BACKTICKED.findall(text):
        names.update(part for part in dotted.split(".") if IDENTIFIER.fullmatch(part))
    return names
