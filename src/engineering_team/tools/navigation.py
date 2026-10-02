"""Project Outline and Repo Map: orient in a codebase without reading whole files."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.symbols import Symbol, extract_symbols, supports
from engineering_team.tools.workspace import ProjectWorkspace

MAX_MAP_FILE_BYTES = 200_000
MAX_SYMBOLS_PER_FILE = 12
MAX_OUTLINE_SYMBOLS = 60
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
CHARS_PER_TOKEN = 4
# One symbol is rarely worth more than this many referencing files when ranking.
REFERENCE_CAP = 25


def _label(symbol: Symbol) -> str:
    owner = f"{symbol.parent}." if symbol.parent else ""
    return f"{symbol.kind} {owner}{symbol.name}"


def project_outline(workspace: ProjectWorkspace, path: str = ".", max_files: int = 40) -> str:
    """Top-level symbols (classes, functions, methods, types) of each source file below ``path``."""

    base = workspace.resolve(path, must_exist=True)
    files = [base] if base.is_file() else list(iter_files(workspace, base))
    limit = max(1, min(int(max_files), 200))
    sections: list[str] = []
    candidates = 0
    for file in files:
        relative = workspace.relative_name(file)
        if not supports(relative):
            continue
        text = read_text_or_none(file)
        if text is None:
            continue
        symbols = extract_symbols(relative, text)
        if not symbols:
            continue
        candidates += 1
        if len(sections) >= limit:
            continue
        shown = symbols[:MAX_OUTLINE_SYMBOLS]
        rows = [f"  {_label(symbol)}  L{symbol.line}" for symbol in shown]
        if len(symbols) > len(shown):
            rows.append(f"  ... {len(symbols) - len(shown)} more symbols")
        sections.append(f"{relative} ({len(text.splitlines())} lines)\n" + "\n".join(rows))
    if not sections:
        return (
            f"No supported source files with symbols under {path} (supported: Python, "
            "JS/TS, Go, Java/Kotlin, Rust). Use Project Tree or Find Files instead."
        )
    header = f"{candidates} source file(s) with symbols under {path}"
    if candidates > len(sections):
        header += f"; showing {len(sections)}, pass a narrower path"
    return header + "\n" + "\n".join(sections)


@dataclass
class _SourceFile:
    path: str
    symbols: list[Symbol]
    identifiers: frozenset[str]


def repo_map(workspace: ProjectWorkspace, path: str = ".", size: int = 1500) -> str:
    """A token-budgeted overview of the most-referenced files and their key symbols.

    Files are ranked by how many *other* files mention the identifiers they define, a cheap
    stand-in for importance (the idea behind aider's repo map).
    """

    base = workspace.resolve(path, must_exist=True)
    budget = max(400, min(int(size), 12_000) * CHARS_PER_TOKEN)
    rules = IgnoreRules.for_workspace(workspace)
    sources: list[_SourceFile] = []
    for file in [base] if base.is_file() else iter_files(workspace, base, rules=rules):
        relative = workspace.relative_name(file)
        if not supports(relative):
            continue
        text = read_text_or_none(file, limit=MAX_MAP_FILE_BYTES)
        if text is None:
            continue
        symbols = [
            symbol
            for symbol in extract_symbols(relative, text)
            if not (symbol.name.startswith("__") and symbol.name.endswith("__"))
        ]
        sources.append(_SourceFile(relative, symbols, frozenset(IDENTIFIER.findall(text))))
    if not sources:
        return f"No supported source files under {path}. Use Project Tree to see what exists."

    # In how many files does each identifier appear? A definition counts its own file once,
    # so subtract it to get references from elsewhere.
    mentions: Counter[str] = Counter()
    for source in sources:
        mentions.update(source.identifiers)
    defined_in: Counter[str] = Counter()
    for source in sources:
        defined_in.update({symbol.name for symbol in source.symbols})

    def references(symbol: Symbol) -> int:
        return max(0, mentions[symbol.name] - defined_in[symbol.name])

    ranked = sorted(
        sources,
        key=lambda source: (
            -sum(min(references(symbol), REFERENCE_CAP) for symbol in source.symbols),
            source.path,
        ),
    )
    ranked = [source for source in ranked if source.symbols]

    header = (
        f"Repo map for {path}: {len(ranked)} source file(s) with symbols, most-referenced first"
    )
    out = [header]
    used = len(header)
    shown = 0
    for source in ranked:
        chosen = sorted(
            sorted(source.symbols, key=lambda s: (-references(s), s.line))[:MAX_SYMBOLS_PER_FILE],
            key=lambda s: s.line,
        )
        rows = [f"{source.path}"]
        for symbol in chosen:
            used_by = references(symbol)
            note = f"  (used in {used_by} files)" if used_by else ""
            rows.append(f"  {_label(symbol)}  L{symbol.line}{note}")
        if len(source.symbols) > len(chosen):
            rows.append(f"  ... {len(source.symbols) - len(chosen)} more")
        block = "\n".join(rows)
        if used + len(block) + 1 > budget and shown:
            break
        out.append(block)
        used += len(block) + 1
        shown += 1
    if shown < len(ranked):
        out.append(
            f"... {len(ranked) - shown} more file(s) not shown; raise size or pass a subdirectory."
        )
    return "\n".join(out)
