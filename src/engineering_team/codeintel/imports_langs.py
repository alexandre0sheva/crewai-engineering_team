"""Import extraction for Java/Kotlin, C#, Rust, Ruby, and PHP."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable

from engineering_team.codeintel.imports_base import ImportRef, Resolver, line_at
from engineering_team.codeintel.index import SourceFile

_JAVA = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(\.\*)?\s*;?\s*$", re.MULTILINE)
_CSHARP = re.compile(
    r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;", re.MULTILINE
)
_RUST_MOD = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.MULTILINE)
_RUST_USE = re.compile(r"^\s*(?:pub\s+)?use\s+crate::([\w:]+)", re.MULTILINE)
_RUBY_RELATIVE = re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", re.MULTILINE)
_RUBY_REQUIRE = re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", re.MULTILINE)
_PHP_USE = re.compile(r"^\s*use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;", re.MULTILINE)
_PHP_INCLUDE = re.compile(
    r"""^\s*(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]/?([^'"]+)['"]""",
    re.MULTILINE,
)

Extractor = Callable[[SourceFile, Resolver], list[ImportRef]]


def _java(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    return [
        ImportRef(
            m.group(1) + (m.group(2) or ""),
            line_at(source.text, m.start()),
            resolver.java(m.group(1), wildcard=bool(m.group(2))),
        )
        for m in _JAVA.finditer(source.text)
    ]


def _csharp(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    return [
        ImportRef(m.group(1), line_at(source.text, m.start()), resolver.csharp(m.group(1)))
        for m in _CSHARP.finditer(source.text)
    ]


def _first_existing(paths: frozenset[str], candidates: list[str]) -> tuple[str, ...]:
    return next(((c,) for c in candidates if c in paths), ())


def _rust_crate_root(paths: frozenset[str], importer: str) -> str | None:
    directory = posixpath.dirname(importer)
    while True:
        if (
            posixpath.join(directory, "lib.rs") in paths
            or posixpath.join(directory, "main.rs") in paths
        ):
            return directory
        if not directory:
            return None
        directory = posixpath.dirname(directory)


def _rust(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    paths = resolver.paths
    stem = posixpath.splitext(posixpath.basename(source.path))[0]
    here = posixpath.dirname(source.path)
    base = here if stem in ("lib", "main", "mod") else posixpath.join(here, stem)
    refs: list[tuple[int, ImportRef]] = []
    for m in _RUST_MOD.finditer(source.text):
        name = m.group(1)
        targets = _first_existing(
            paths, [posixpath.join(base, f"{name}.rs"), posixpath.join(base, name, "mod.rs")]
        )
        refs.append((m.start(), ImportRef(f"mod {name}", line_at(source.text, m.start()), targets)))
    root = _rust_crate_root(paths, source.path)
    for m in _RUST_USE.finditer(source.text):
        parts = m.group(1).split("::")
        resolved: tuple[str, ...] = ()
        if root is not None:
            for end in range(len(parts), 0, -1):
                prefix = posixpath.join(root, *parts[:end])
                resolved = _first_existing(
                    paths, [f"{prefix}.rs", posixpath.join(prefix, "mod.rs")]
                )
                if resolved:
                    break
        spec = f"crate::{m.group(1)}"
        refs.append((m.start(), ImportRef(spec, line_at(source.text, m.start()), resolved)))
    return [ref for _, ref in sorted(refs, key=lambda item: item[0])]


def _ruby(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    paths = resolver.paths
    here = posixpath.dirname(source.path)
    refs: list[tuple[int, ImportRef]] = []
    for m in _RUBY_RELATIVE.finditer(source.text):
        spec = m.group(1)
        wanted = posixpath.normpath(posixpath.join(here, spec))
        targets = _first_existing(paths, [wanted, wanted + ".rb"])
        refs.append((m.start(), ImportRef(spec, line_at(source.text, m.start()), targets)))
    for m in _RUBY_REQUIRE.finditer(source.text):
        spec = m.group(1)
        wanted = spec if spec.endswith(".rb") else spec + ".rb"
        found = sorted(p for p in paths if p == wanted or p.endswith("/" + wanted))
        targets = (found[0],) if found else ()
        refs.append((m.start(), ImportRef(spec, line_at(source.text, m.start()), targets)))
    return [ref for _, ref in sorted(refs, key=lambda item: item[0])]


def _php(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    paths = resolver.paths
    here = posixpath.dirname(source.path)
    refs: list[tuple[int, ImportRef]] = []
    for m in _PHP_USE.finditer(source.text):
        refs.append(
            (
                m.start(),
                ImportRef(m.group(1), line_at(source.text, m.start()), resolver.php(m.group(1))),
            )
        )
    for m in _PHP_INCLUDE.finditer(source.text):
        spec = m.group(1)
        targets = _first_existing(
            paths, [posixpath.normpath(posixpath.join(here, spec)), posixpath.normpath(spec)]
        )
        refs.append((m.start(), ImportRef(spec, line_at(source.text, m.start()), targets)))
    return [ref for _, ref in sorted(refs, key=lambda item: item[0])]


OTHER_EXTRACTORS: dict[str, Extractor] = {
    "java": _java,
    "kotlin": _java,
    "csharp": _csharp,
    "rust": _rust,
    "ruby": _ruby,
    "php": _php,
}
