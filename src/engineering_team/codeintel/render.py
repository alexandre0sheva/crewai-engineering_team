"""Compact, counts-first text for the code intelligence tools."""

from __future__ import annotations

import difflib
from collections import Counter

from engineering_team.codeintel.definitions import Definition
from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.references import Reference
from engineering_team.codeintel.related import RelatedTest
from engineering_team.tools.support import ToolError, numbered

MAX_SHOW_LINES = 200
KINDS = "class, function, method, interface, struct, enum, trait, type, module, const"


def _extent(definition: Definition) -> str:
    if definition.end_line == definition.line:
        return f"L{definition.line}"
    return f"L{definition.line}-L{definition.end_line}"


def search_definitions(
    index: SourceIndex, name: str, *, kind: str = "", path_prefix: str = ""
) -> list[Definition]:
    """Definitions whose name matches ``name``: exact first, then prefix, then substring."""

    needle = name.strip()
    lowered = needle.lower()
    wanted_kind = kind.strip().lower()
    ranked: list[tuple[int, Definition]] = []
    for path in index.files:
        if path_prefix and not (path == path_prefix or path.startswith(path_prefix + "/")):
            continue
        for definition in index.definitions(path):
            if wanted_kind and definition.kind != wanted_kind:
                continue
            lower_name = definition.name.lower()
            if needle in (definition.name, definition.qualified):
                rank = 0
            elif lower_name == lowered:
                rank = 1
            elif lower_name.startswith(lowered):
                rank = 2
            elif lowered in lower_name:
                rank = 3
            else:
                continue
            ranked.append((rank, definition))
    ranked.sort(key=lambda item: (item[0], item[1].path, item[1].line))
    return [definition for _, definition in ranked]


def render_symbols(definitions: list[Definition], name: str, limit: int) -> str:
    if not definitions:
        return (
            f"No definition matching '{name}'. Names are matched case-insensitively as a "
            "substring; try a shorter name, or use Search Project Files for text that is not a "
            f"definition. Kinds: {KINDS}."
        )
    lines = [f"{len(definitions)} definition(s) matching '{name}'"]
    for definition in definitions[:limit]:
        lines.append(
            f"{definition.path}:{definition.line} {definition.kind} {definition.qualified} "
            f"({_extent(definition)})"
        )
    if len(definitions) > limit:
        lines.append(f"... {len(definitions) - limit} more; narrow with kind or path.")
    return "\n".join(lines)


def render_symbol_body(
    index: SourceIndex, name: str, definitions: list[Definition], context: int
) -> str:
    exact = [d for d in definitions if name.strip() in (d.name, d.qualified)]
    if not exact:
        pool = sorted({d.name for d in index.all_definitions()})
        close = difflib.get_close_matches(name.strip(), pool, n=3, cutoff=0.6)
        hint = f" Closest: {', '.join(close)}." if close else ""
        raise ToolError(f"No definition named '{name}'.{hint} Use Find Symbol for partial matches.")
    shown, others = exact[0], exact[1:]
    lines = index.files[shown.path].text.splitlines()
    pad = max(0, min(int(context), 20))
    first = max(1, shown.line - pad)
    last = min(len(lines), shown.end_line + pad)
    cut = last - first + 1 > MAX_SHOW_LINES
    if cut:
        last = first + MAX_SHOW_LINES - 1
    out = []
    if others:
        listed = ", ".join(f"{d.path}:{d.line} {d.kind} {d.qualified}" for d in others[:5])
        out.append(
            f"{len(exact)} definitions named '{name}'; showing the first. Others: {listed}. "
            "pass file= to choose another."
        )
    out.append(f"{shown.path}:{shown.line}-{shown.end_line} {shown.kind} {shown.qualified}")
    out.append(numbered(lines[first - 1 : last], first))
    if cut:
        out.append(
            f"... body cut at {MAX_SHOW_LINES} lines; Read File Range {shown.path} "
            f"start_line={last + 1} continues."
        )
    return "\n".join(out)


def render_references(references: list[Reference], symbol: str, limit: int) -> str:
    if not references:
        return (
            f"No references to '{symbol}' in the indexed source files (matching is "
            "case-sensitive and whole-word). Use Search Project Files for other file types."
        )
    kinds = Counter(ref.kind for ref in references)
    summary = ", ".join(
        f"{kinds[kind]} {kind}" for kind in ("definition", "import", "call", "other") if kinds[kind]
    )
    files = len({ref.path for ref in references})
    lines = [f"{len(references)} reference(s) to '{symbol}' in {files} file(s): {summary}"]
    current = ""
    for ref in references[:limit]:
        if ref.path != current:
            current = ref.path
            lines.append(ref.path)
        lines.append(f"  L{ref.line} {ref.kind}: {ref.text.strip()[:120]}")
    if len(references) > limit:
        lines.append(f"... {len(references) - limit} more; narrow with path.")
    lines.append(
        "Dependents of the defining file: Who Imports. Tests that cover it: Find Related Tests."
    )
    return "\n".join(lines)


def render_importers(graph: ImportGraph, target: str, depth: int, limit: int) -> str:
    levels = max(1, min(int(depth), 4))
    found: dict[str, tuple[int, str, str, int]] = {}  # path -> (level, via, spec, line)
    frontier = [target]
    for level in range(1, levels + 1):
        following: list[str] = []
        for file in frontier:
            for importer, ref in graph.importers_of(file):
                if importer != target and importer not in found:
                    found[importer] = (level, file, ref.spec, ref.line)
                    following.append(importer)
        frontier = following
    if not found:
        return (
            f"No file imports {target}. It may be an entry point, or be loaded dynamically "
            "(string-built imports and path aliases are not seen)."
        )
    direct = sorted(path for path, item in found.items() if item[0] == 1)
    indirect = sorted(path for path, item in found.items() if item[0] > 1)
    if levels == 1:
        lines = [f"{len(found)} file(s) import {target}"]
    else:
        lines = [
            f"{len(found)} file(s) depend on {target} "
            f"({len(direct)} direct, {len(indirect)} indirect)"
        ]
    for path in [*direct, *indirect]:
        level, via, spec, line = found[path]
        lines.append(f"{path}:{line} imports {spec}" if level == 1 else f"{path} (via {via})")
    lines = lines[: limit + 1] + (
        [f"... {len(found) - limit} more; lower depth or ask about a narrower file."]
        if len(found) > limit
        else []
    )
    lines.append("Static analysis: dynamic imports and path aliases are not seen.")
    return "\n".join(lines)


def render_imports_of(graph: ImportGraph, path: str) -> str:
    refs = graph.imports_of(path)
    project = [(target, ref) for ref in refs for target in ref.targets]
    external = sorted({ref.spec for ref in refs if not ref.targets})
    distinct = len({target for target, _ in project})
    lines = [f"{path} imports {distinct} project file(s) and {len(external)} external"]
    if project:
        lines.append("project:")
        lines.extend(f"  {target}  ({ref.spec}, line {ref.line})" for target, ref in project)
    if external:
        lines.append("external: " + ", ".join(external))
    return "\n".join(lines)


def render_related(tests: list[RelatedTest], target: str, limit: int) -> str:
    if not tests:
        return (
            f"No related tests found for {target}: no test file imports it, is named after it, "
            "or mentions what it defines. Treat it as untested unless Run Tests says otherwise."
        )
    lines = [f"{len(tests)} test file(s) related to {target}"]
    lines.extend(f"{test.path}  {'; '.join(test.reasons)}" for test in tests[:limit])
    return "\n".join(lines)
