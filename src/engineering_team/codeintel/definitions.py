"""Definitions with their extent: classes, functions, methods, types, and constants.

Python uses ``ast`` (a file that does not parse falls back to indentation rules); Ruby is
read by indentation; the brace languages (JS/TS, Go, Java/Kotlin, C#, Rust, PHP) use the
declaration regexes of :mod:`engineering_team.tools.symbols` plus member rules, with braces
counted to find where a definition ends. Navigation help, not a parser: exotic syntax is missed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from engineering_team.codeintel.languages import language_of
from engineering_team.tools.symbols import REGEX_RULES

KIND_NAMES = {"fn": "function", "mod": "module", "record": "class"}
OWNER_KINDS = frozenset({"class", "interface", "struct", "enum", "trait", "module", "impl"})
MAX_BODY_LINES = 3000
# Languages whose declarations may sit inside a ``namespace``/``mod`` block.
NESTED_OK = frozenset({"csharp", "php", "rust", "java", "kotlin"})
CONTROL_WORDS = frozenset(
    {"if", "for", "while", "switch", "catch", "function", "return", "with", "new", "throw"}
    | {"else", "using", "await", "yield", "do", "try", "case", "lock", "foreach"}
)

_RULE_SUFFIX = {"js": ".ts", "go": ".go", "java": ".java", "kotlin": ".kt", "rust": ".rs"}
_MODS = (
    r"(?:(?:public|protected|private|internal|static|final|abstract|sealed|partial|readonly"
    r"|unsafe|file|async|override|virtual|synchronized|extern|native|default|new)\s+)*"
)
_EXTRA_TYPES: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "csharp": [
        (kind, re.compile(rf"^\s*{_MODS}{kind}\s+([A-Za-z_]\w*)"))
        for kind in ("class", "interface", "struct", "enum", "record")
    ],
    "php": [
        (
            kind,
            re.compile(rf"^\s*(?:(?:abstract|final|readonly)\s+)*{kind}\s+([A-Za-z_]\w*)"),
        )
        for kind in ("class", "interface", "trait", "enum")
    ],
}
_GO_METHOD = re.compile(
    r"^func\s+\(\s*\w*\s*\*?\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*\)\s*([A-Za-z_]\w*)"
)
_RUST_IMPL = re.compile(
    r"^\s*(?:unsafe\s+)?impl(?:<[^>]*>)?\s+(?:[\w:<>,&' ]+?\s+for\s+)?([A-Za-z_]\w*)"
)
_RUST_FN = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)"
)
_PHP_FUNCTION = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+&?([A-Za-z_]\w*)"
)
_KOTLIN_FUN = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|override|open|abstract|suspend|inline"
    r"|operator|infix)\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\("
)
_JS_METHOD = re.compile(
    r"^\s+(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*"
    r"\*?\s*([A-Za-z_$#][\w$]*)\s*(?:<[^>(]*>)?\s*\([^)]*\)\s*(?::\s*[^{=;]+)?\{\s*$"
)
_MEMBER = re.compile(
    rf"^\s+{_MODS}(?:<[^>]+>\s+)?[\w.<>,?\[\]]+\s+([A-Za-z_]\w*)\s*(?:<[^>(]*>)?\s*\("
)
_NOISE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|//.*|/\*.*?\*/")
_PY_RULES = (
    ("class", re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)")),
    ("def", re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)")),
)
_RUBY_RULES = (
    ("class", re.compile(r"^(\s*)class\s+([A-Z]\w*)")),
    ("module", re.compile(r"^(\s*)module\s+([A-Z]\w*)")),
    ("def", re.compile(r"^(\s*)def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)")),
)


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str  # class, function, method, interface, struct, enum, trait, type, module, const
    path: str
    line: int  # 1-based first line
    end_line: int  # 1-based last line of the body
    parent: str | None = None  # the enclosing class/type, for members

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


def definitions_in(path: str, text: str) -> list[Definition]:
    """Every definition in ``text`` in file order; empty for languages that are not read."""

    language = language_of(path)
    if language is None:
        return []
    if language == "python":
        found = _python(path, text)
        return found if found is not None else _indented(path, text, _PY_RULES)
    if language == "ruby":
        return _indented(path, text, _RUBY_RULES, ruby=True)
    return _braced(path, text, language)


def _python(path: str, text: str) -> list[Definition] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found: list[Definition] = []

    def visit(body: list[ast.stmt], parent: str | None) -> None:
        for node in body:
            end = getattr(node, "end_lineno", None) or node.lineno
            if isinstance(node, ast.ClassDef):
                found.append(Definition(node.name, "class", path, node.lineno, end, parent))
                visit(node.body, node.name)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                kind = "method" if parent else "function"
                found.append(Definition(node.name, kind, path, node.lineno, end, parent))
            elif parent is None and isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                found.extend(
                    Definition(target.id, "const", path, node.lineno, end)
                    for target in targets
                    if isinstance(target, ast.Name) and target.id.isupper()
                )

    visit(tree.body, None)
    return sorted(found, key=lambda definition: definition.line)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _indented(
    path: str,
    text: str,
    rules: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    ruby: bool = False,
) -> list[Definition]:
    """Python (fallback) and Ruby: nesting and extent come from indentation."""

    lines = text.splitlines()
    found: list[Definition] = []
    owners: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        for kind, pattern in rules:
            match = pattern.match(line)
            if match is None:
                continue
            indent, name = len(match.group(1)), match.group(2)
            while owners and indent <= owners[-1][0]:
                owners.pop()
            parent = owners[-1][1] if owners else None
            if kind == "def":
                if parent is None and indent > 0:
                    break  # a nested function, not a member
                kind = "method" if parent else "function"
            end = _indented_end(lines, index, indent, ruby=ruby)
            found.append(Definition(name, kind, path, index + 1, end, parent))
            if kind in ("class", "module"):
                owners.append((indent, name))
            break
    return found


def _indented_end(lines: list[str], start: int, indent: int, *, ruby: bool) -> int:
    last = start
    for index in range(start + 1, min(len(lines), start + MAX_BODY_LINES)):
        line = lines[index]
        if not line.strip():
            continue
        if ruby and _indent(line) == indent and line.strip().startswith("end"):
            return index + 1
        if _indent(line) <= indent and not ruby:
            break
        last = index
    return last + 1


def _delta(line: str) -> int:
    clean = _NOISE.sub("", line)
    return clean.count("{") - clean.count("}")


def _brace_end(lines: list[str], start: int) -> int:
    """The last line of the block that starts on ``start`` (one line when it has no body)."""

    depth = 0
    opened = False
    for index in range(start, min(len(lines), start + MAX_BODY_LINES)):
        clean = _NOISE.sub("", lines[index])
        if not opened and index > start and not clean.strip():
            return index  # a body-less declaration such as ``type Id int``
        for char in clean:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return index + 1
            elif char == ";" and not opened:
                return index + 1
    return start + 1


def _first_match(rules: list[tuple[str, re.Pattern[str]]], line: str) -> tuple[str, str] | None:
    for kind, pattern in rules:
        if match := pattern.match(line):
            return KIND_NAMES.get(kind, kind), match.group(1)
    return None


def _member(language: str, line: str) -> str | None:
    """The name of a method declared on ``line`` (which sits directly in a type body)."""

    if language == "js":
        match = _JS_METHOD.match(line)
    elif language == "rust":
        match = _RUST_FN.match(line)
    elif language == "php":
        match = _PHP_FUNCTION.match(line)
    else:
        stripped = line.strip()
        first = stripped.split(None, 1)[0] if stripped else ""
        if first in CONTROL_WORDS or stripped.endswith(";") or "=" in stripped.split("(")[0]:
            return None
        match = _KOTLIN_FUN.match(line) if language == "kotlin" else None
        match = match or _MEMBER.match(line)
    if match is None or match.group(1) in CONTROL_WORDS:
        return None
    return match.group(1)


def _braced(path: str, text: str, language: str) -> list[Definition]:
    lines = text.splitlines()
    rules = [*REGEX_RULES.get(_RULE_SUFFIX.get(language, ""), []), *_EXTRA_TYPES.get(language, [])]
    types = [rule for rule in rules if KIND_NAMES.get(rule[0], rule[0]) in OWNER_KINDS]
    others = [rule for rule in rules if rule not in types]
    found: list[Definition] = []
    owners: list[tuple[str, int, str]] = []  # (name, body depth, kind)
    depth = 0
    for index, line in enumerate(lines):
        # An Allman-style ``{`` on its own line opens the body of the type declared above it.
        while owners and depth < owners[-1][1] and not line.lstrip().startswith("{"):
            owners.pop()
        member_of = owners[-1] if owners and depth == owners[-1][1] else None
        declared: tuple[str, str, str | None] | None = None  # name, kind, parent
        if hit := _first_match(types, line):
            declared = (hit[1], hit[0], member_of[0] if member_of else None)
        elif language == "go" and (method := _GO_METHOD.match(line)):
            declared = (method.group(2), "method", method.group(1))
        elif language == "rust" and (impl := _RUST_IMPL.match(line)):
            owners.append((impl.group(1), depth + 1, "impl"))
        elif member_of is not None:
            name = _member(language, line)
            if name and name != member_of[0]:
                kind = "function" if member_of[2] == "module" else "method"
                declared = (name, kind, member_of[0])
        elif not owners and (depth == 0 or language in NESTED_OK):
            hit = _first_match(others, line)
            if hit is None and language == "kotlin" and (fun := _KOTLIN_FUN.match(line)):
                hit = ("function", fun.group(1))
            if hit:
                declared = (hit[1], hit[0], None)
        if declared is not None:
            name, kind, parent = declared
            found.append(Definition(name, kind, path, index + 1, _brace_end(lines, index), parent))
            if kind in OWNER_KINDS:
                owners.append((name, depth + 1, kind))
        depth += _delta(line)
    return found
