"""What an import is, and the lookup tables that resolve one to a project file."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass

from engineering_team.codeintel.index import SourceIndex


@dataclass(frozen=True)
class ImportRef:
    spec: str  # as written: "app.config", "./api", "example.com/svc/svc"
    line: int  # 1-based
    targets: tuple[str, ...]  # project files it resolves to; empty means external or unresolved


def line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_CSHARP_NAMESPACE = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
_PHP_NAMESPACE = re.compile(r"^\s*namespace\s+([\w\\]+)\s*[;{]", re.MULTILINE)


class Resolver:
    """Per-language lookups (Go packages, Java/C#/PHP namespaces) built once per index."""

    def __init__(self, index: SourceIndex) -> None:
        self.paths = frozenset(index.files)
        self._go_modules = index.go_modules
        self._go_packages: dict[str, list[str]] = defaultdict(list)
        self._java_types: dict[str, str] = {}
        self._java_packages: dict[str, list[str]] = defaultdict(list)
        self._csharp: dict[str, list[str]] = defaultdict(list)
        self._php: dict[str, str] = {}
        for path, source in index.files.items():
            stem = posixpath.splitext(posixpath.basename(path))[0]
            if source.language == "go" and not path.endswith("_test.go"):
                self._go_packages[posixpath.dirname(path)].append(path)
            elif source.language in ("java", "kotlin") and (m := _JAVA_PACKAGE.search(source.text)):
                self._java_types[f"{m.group(1)}.{stem}"] = path
                self._java_packages[m.group(1)].append(path)
            elif source.language == "csharp":
                for match in _CSHARP_NAMESPACE.finditer(source.text):
                    self._csharp[match.group(1)].append(path)
            elif source.language == "php" and (m := _PHP_NAMESPACE.search(source.text)):
                self._php[f"{m.group(1)}\\{stem}"] = path

    def go_package(self, spec: str) -> tuple[str, ...]:
        """The files of the project package an import path names (all non-test files)."""

        best: tuple[str, str] | None = None
        for directory, module in self._go_modules.items():
            if (spec == module or spec.startswith(module + "/")) and (
                best is None or len(module) > len(best[1])
            ):
                best = (directory, module)
        if best is None:
            return ()
        directory, module = best
        inside = posixpath.join(directory, spec[len(module) :].lstrip("/"))
        return tuple(self._go_packages.get(inside.strip("/") if inside != "." else "", ()))

    def java(self, spec: str, *, wildcard: bool) -> tuple[str, ...]:
        if wildcard:
            return tuple(self._java_packages.get(spec, ()))
        parts = spec.split(".")
        for end in range(len(parts), 0, -1):  # ``a.b.C.Inner`` and static members live in C
            if (target := self._java_types.get(".".join(parts[:end]))) is not None:
                return (target,)
        return ()

    def csharp(self, namespace: str) -> tuple[str, ...]:
        return tuple(self._csharp.get(namespace, ()))

    def php(self, name: str) -> tuple[str, ...]:
        target = self._php.get(name.lstrip("\\"))
        return (target,) if target else ()
