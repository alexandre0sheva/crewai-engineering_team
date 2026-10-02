"""Light symbol extraction for outlines and the repo map.

Python uses ``ast``; JavaScript/TypeScript, Go, Java, and Rust use small regexes that find
top-level declarations. This is navigation help, not a parser: it can miss exotic syntax.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # class, function, method, interface, type, enum, struct, trait, const, ...
    line: int  # 1-based
    parent: str | None = None  # the enclosing class, for methods


_JS = [
    (
        "function",
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)"),
    ),
    (
        "class",
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
    ),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=")),
    ("enum", re.compile(r"^(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)")),
    (
        "function",
        re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
            r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
        ),
    ),
    (
        "function",
        re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function"
        ),
    ),
]
_GO = [
    ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
    ("struct", re.compile(r"^type\s+([A-Za-z_]\w*)\s+struct\b")),
    ("interface", re.compile(r"^type\s+([A-Za-z_]\w*)\s+interface\b")),
    ("type", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?!struct\b|interface\b)\S")),
]
_JAVA = [
    (
        kind,
        re.compile(
            rf"^\s*(?:(?:public|protected|private|static|final|abstract|sealed)\s+)*{kind}\s+([A-Za-z_]\w*)"
        ),
    )
    for kind in ("class", "interface", "enum", "record")
]
_RUST = [
    (
        kind,
        re.compile(
            rf"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?{kind}\s+([A-Za-z_]\w*)"
        ),
    )
    for kind in ("fn", "struct", "enum", "trait", "mod", "type")
]

_REGEX_LANGUAGES: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    **dict.fromkeys((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), _JS),
    ".go": _GO,
    ".java": _JAVA,
    ".kt": _JAVA,
    ".rs": _RUST,
}
SUPPORTED_SUFFIXES = frozenset({".py", *_REGEX_LANGUAGES})

_PY_FALLBACK = [
    ("class", re.compile(r"^class\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)")),
]


def supports(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SUPPORTED_SUFFIXES


def _python_symbols(text: str) -> list[Symbol] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    symbols: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(Symbol(node.name, "class", node.lineno))
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols.append(Symbol(member.name, "method", member.lineno, node.name))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(Symbol(node.name, "function", node.lineno))
        elif isinstance(node, ast.Assign):
            symbols.extend(
                Symbol(target.id, "const", node.lineno)
                for target in node.targets
                if isinstance(target, ast.Name) and target.id.isupper()
            )
    return symbols


def _regex_symbols(text: str, rules: list[tuple[str, re.Pattern[str]]]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in rules:
            if match := pattern.match(line):
                symbols.append(Symbol(match.group(1), kind, number))
                break
    return symbols


def extract_symbols(path: str, text: str) -> list[Symbol]:
    """Top-level declarations of ``text`` (and methods of Python classes), in file order."""

    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        found = _python_symbols(text)
        return found if found is not None else _regex_symbols(text, _PY_FALLBACK)
    rules = _REGEX_LANGUAGES.get(suffix)
    return _regex_symbols(text, rules) if rules else []
