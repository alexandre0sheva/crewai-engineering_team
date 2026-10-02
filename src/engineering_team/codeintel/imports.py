"""The import graph: what each file imports, and (inverted) who imports each file.

Static and best-effort: ``ast`` for Python, regular expressions for the rest. A specifier that
does not resolve to a project file is *external* (standard library, a package, a path alias, or
something built at run time). Dynamic imports whose target is not a string literal are not seen.
"""

from __future__ import annotations

import ast
import posixpath
import re
from collections import defaultdict
from pathlib import PurePosixPath

from engineering_team.codeintel.imports_base import ImportRef, Resolver, line_at
from engineering_team.codeintel.imports_langs import OTHER_EXTRACTORS
from engineering_team.codeintel.index import SourceFile, SourceIndex

__all__ = ["ImportGraph", "ImportRef", "import_lines"]

JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
_JS_PATTERNS = (
    re.compile(r"""\b(?:import|export)\s+(?:type\s+)?[\w*${}\s,]*?\bfrom\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
)
_GO_IMPORT = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
_GO_BLOCK = re.compile(r"^\s*import\s*\((.*?)\)", re.MULTILINE | re.DOTALL)
_GO_QUOTED = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)


class ImportGraph:
    """Forward and reverse imports for every file of a :class:`SourceIndex`."""

    def __init__(self, index: SourceIndex) -> None:
        self._index = index
        resolver = Resolver(index)
        self._forward: dict[str, list[ImportRef]] = {}
        self._reverse: dict[str, list[tuple[str, ImportRef]]] = defaultdict(list)
        python = _PythonModules(index)
        for path, source in index.files.items():
            refs = self._extract(source, resolver, python)
            self._forward[path] = refs
            for ref in refs:
                for target in ref.targets:
                    if target != path:
                        self._reverse[target].append((path, ref))

    def imports_of(self, path: str) -> list[ImportRef]:
        return list(self._forward.get(path, []))

    def importers_of(self, path: str) -> list[tuple[str, ImportRef]]:
        return sorted(self._reverse.get(path, []), key=lambda item: (item[0], item[1].line))

    def _extract(
        self, source: SourceFile, resolver: Resolver, python: _PythonModules
    ) -> list[ImportRef]:
        if source.language == "python":
            return python.imports(source)
        if source.language == "js":
            return _js_imports(source, resolver.paths)
        if source.language == "go":
            return _go_imports(source, resolver)
        extractor = OTHER_EXTRACTORS.get(source.language)
        return extractor(source, resolver) if extractor else []


_GENERIC_IMPORT_LINE = re.compile(
    r"^\s*(?:import|using|use|require|require_relative|include|require_once|include_once|mod)\b"
)


def import_lines(source: SourceFile) -> frozenset[int]:
    """The 1-based lines that belong to an import statement (multi-line ones included)."""

    text = source.text
    if source.language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return frozenset()
        return frozenset(
            line
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        )
    spans: list[re.Match[str]] = []
    if source.language == "js":
        spans = [match for pattern in _JS_PATTERNS for match in pattern.finditer(text)]
    elif source.language == "go":
        spans = [*_GO_IMPORT.finditer(text), *_GO_BLOCK.finditer(text)]
    if spans:
        return frozenset(
            line
            for match in spans
            for line in range(line_at(text, match.start()), line_at(text, match.end()) + 1)
        )
    return frozenset(
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if _GENERIC_IMPORT_LINE.match(line)
    )


def _sorted(refs: list[tuple[int, ImportRef]]) -> list[ImportRef]:
    return [ref for _, ref in sorted(refs, key=lambda item: item[0])]


def _js_imports(source: SourceFile, paths: frozenset[str]) -> list[ImportRef]:
    found: list[tuple[int, ImportRef]] = []
    for pattern in _JS_PATTERNS:
        for match in pattern.finditer(source.text):
            spec = match.group(1)
            targets = _resolve_js(paths, source.path, spec)
            found.append(
                (match.start(), ImportRef(spec, line_at(source.text, match.start()), targets))
            )
    return _sorted(found)


def _resolve_js(paths: frozenset[str], importer: str, spec: str) -> tuple[str, ...]:
    if not spec.startswith("."):
        return ()  # a package or an alias such as "@/x": not resolvable without bundler config
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    candidates = [base]
    stem, extension = posixpath.splitext(base)
    if extension in (".js", ".jsx", ".mjs", ".cjs"):  # TS sources are imported as ".js"
        candidates += [stem + ".ts", stem + ".tsx"]
    candidates += [base + suffix for suffix in JS_SUFFIXES]
    candidates += [f"{base}/index{suffix}" for suffix in JS_SUFFIXES]
    return next(((candidate,) for candidate in candidates if candidate in paths), ())


def _go_imports(source: SourceFile, resolver: Resolver) -> list[ImportRef]:
    found: list[tuple[int, str]] = [
        (match.start(1), match.group(1)) for match in _GO_IMPORT.finditer(source.text)
    ]
    for block in _GO_BLOCK.finditer(source.text):
        found.extend(
            (block.start(1) + quoted.start(1), quoted.group(1))
            for quoted in _GO_QUOTED.finditer(block.group(1))
        )
    return [
        ImportRef(spec, line_at(source.text, offset), resolver.go_package(spec))
        for offset, spec in sorted(found)
    ]


class _PythonModules:
    """Maps dotted module names to files, honouring package roots such as ``src/``."""

    def __init__(self, index: SourceIndex) -> None:
        self._paths = frozenset(index.files)
        self.modules: dict[str, str] = {}
        for path in sorted(index.files):
            if path.endswith(".py"):
                for name in self._names(path):
                    self.modules.setdefault(name, path)

    def _is_package(self, parts: tuple[str, ...]) -> bool:
        return "/".join((*parts, "__init__.py")) in self._paths

    def _names(self, path: str) -> list[str]:
        file = PurePosixPath(path)
        is_init = file.name == "__init__.py"
        parts = file.with_suffix("").parts[:-1] if is_init else file.with_suffix("").parts
        directories = len(parts) if is_init else len(parts) - 1
        names = []
        for start in range(len(parts)):
            is_root = start == 0 or not self._is_package(parts[:start])
            inside = all(self._is_package(parts[:end]) for end in range(start + 1, directories + 1))
            if is_root and inside:
                names.append(".".join(parts[start:]))
        return names

    def imports(self, source: SourceFile) -> list[ImportRef]:
        try:
            tree = ast.parse(source.text)
        except (SyntaxError, ValueError):
            return []
        refs: list[ImportRef] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                refs.extend(
                    ImportRef(alias.name, node.lineno, self._absolute(alias.name))
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
                spec = "." * node.level + (node.module or "")
                refs.append(ImportRef(spec, node.lineno, self._from(source.path, node, names)))
        return sorted(refs, key=lambda ref: ref.line)

    def _absolute(self, dotted: str) -> tuple[str, ...]:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            if (target := self.modules.get(".".join(parts[:end]))) is not None:
                return (target,)
        return ()

    def _from(self, importer: str, node: ast.ImportFrom, names: list[str]) -> tuple[str, ...]:
        if node.level:
            base = PurePosixPath(importer).parent
            for _ in range(node.level - 1):
                base = base.parent
            directory = base.as_posix()
            module_path = (
                f"{directory}/{node.module.replace('.', '/')}" if node.module else directory
            )
            module_path = module_path.removeprefix("./")
            package = self._file_or_package(module_path)
            submodules = [self._file_or_package(f"{module_path}/{name}") for name in names]
        else:
            module = node.module or ""
            package = self.modules.get(module) or (self._absolute(module) or (None,))[0]
            submodules = [self.modules.get(f"{module}.{name}") for name in names]
        found = [target for target in submodules if target]
        if package and (not found or len(found) < len(names)):
            found.append(package)
        return tuple(dict.fromkeys(found))

    def _file_or_package(self, module_path: str) -> str | None:
        for candidate in (f"{module_path}.py", f"{module_path}/__init__.py"):
            if candidate.removeprefix("/") in self._paths:
                return candidate.removeprefix("/")
        return None
