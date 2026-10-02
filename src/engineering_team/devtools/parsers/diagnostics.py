"""Linters, type checkers, and compilers: one ``Diagnostic`` list from many output formats."""

from __future__ import annotations

import json
import re
from typing import Any

from engineering_team.devtools.models import Diagnostic, DiagnosticSeverity
from engineering_team.devtools.parsers.common import excerpt, rel_path, strip_ansi

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class DiagnosticsError(ValueError):
    """The output is not in the format the tool is supposed to write."""


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise DiagnosticsError(f"{what}: not valid JSON ({exc})") from exc


def _item(
    file: str | None,
    line: int | None,
    col: int | None,
    rule: str | None,
    severity: DiagnosticSeverity,
    message: str,
    root: str | None,
) -> Diagnostic:
    return Diagnostic(
        file=rel_path(file, root) if file else None,
        line=line,
        col=col,
        rule=rule or None,
        severity=severity,
        message=excerpt(" ".join(message.split()), 300),
    )


# -- JSON reporters ----------------------------------------------------------------------


def parse_ruff(text: str, root: str | None = None) -> list[Diagnostic]:
    """``ruff check --output-format json``."""

    data = _json(text, "ruff")
    if not isinstance(data, list):
        raise DiagnosticsError("ruff: expected a JSON list; use --output-format json")
    return [
        _item(
            d.get("filename"),
            (d.get("location") or {}).get("row"),
            (d.get("location") or {}).get("column"),
            d.get("code"),
            "error",
            d.get("message", ""),
            root,
        )
        for d in data
    ]


def parse_eslint(text: str, root: str | None = None) -> list[Diagnostic]:
    """``eslint -f json``."""

    data = _json(text, "eslint")
    if not isinstance(data, list):
        raise DiagnosticsError("eslint: expected a JSON list; use -f json")
    found = []
    for entry in data:
        for message in entry.get("messages", []):
            severity: DiagnosticSeverity = "error" if message.get("severity") == 2 else "warning"
            found.append(
                _item(
                    entry.get("filePath"),
                    message.get("line"),
                    message.get("column"),
                    message.get("ruleId"),
                    severity,
                    message.get("message", ""),
                    root,
                )
            )
    return found


def parse_pyright(text: str, root: str | None = None) -> list[Diagnostic]:
    """``pyright --outputjson`` (lines and columns are 0-based there)."""

    data = _json(text, "pyright")
    if not isinstance(data, dict) or "generalDiagnostics" not in data:
        raise DiagnosticsError("pyright: no 'generalDiagnostics'; use --outputjson")
    found = []
    for d in data["generalDiagnostics"]:
        start = (d.get("range") or {}).get("start") or {}
        level = d.get("severity")
        severity: DiagnosticSeverity = (
            "error" if level == "error" else "warning" if level == "warning" else "info"
        )
        line, col = start.get("line"), start.get("character")
        found.append(
            _item(
                d.get("file"),
                line + 1 if line is not None else None,
                col + 1 if col is not None else None,
                d.get("rule"),
                severity,
                d.get("message", ""),
                root,
            )
        )
    return found


def parse_rubocop(text: str, root: str | None = None) -> list[Diagnostic]:
    """``rubocop --format json``."""

    data = _json(text, "rubocop")
    if not isinstance(data, dict) or "files" not in data:
        raise DiagnosticsError("rubocop: no 'files'; use --format json")
    found = []
    for entry in data["files"]:
        for offense in entry.get("offenses", []):
            level = offense.get("severity")
            severity: DiagnosticSeverity = "error" if level in ("error", "fatal") else "warning"
            location = offense.get("location") or {}
            found.append(
                _item(
                    entry.get("path"),
                    location.get("line"),
                    location.get("column"),
                    offense.get("cop_name"),
                    severity,
                    offense.get("message", ""),
                    root,
                )
            )
    return found


def parse_phpcs(text: str, root: str | None = None) -> list[Diagnostic]:
    """``phpcs --report=json``."""

    data = _json(text, "phpcs")
    if not isinstance(data, dict) or "files" not in data:
        raise DiagnosticsError("phpcs: no 'files'; use --report=json")
    found = []
    for path, entry in data["files"].items():
        for message in entry.get("messages", []):
            severity: DiagnosticSeverity = "error" if message.get("type") == "ERROR" else "warning"
            found.append(
                _item(
                    path,
                    message.get("line"),
                    message.get("column"),
                    message.get("source"),
                    severity,
                    message.get("message", ""),
                    root,
                )
            )
    return found


def parse_cargo_json(text: str, root: str | None = None) -> list[Diagnostic]:
    """``cargo check|clippy|build --message-format=json`` (one JSON object per line)."""

    found = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("reason") != "compiler-message":
            continue
        message = event.get("message") or {}
        level = message.get("level")
        if level not in ("error", "warning"):
            continue
        spans = message.get("spans") or []
        primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else None)
        if primary is None:  # "aborting due to 2 previous errors", "3 warnings emitted"
            continue
        found.append(
            _item(
                primary.get("file_name"),
                primary.get("line_start"),
                primary.get("column_start"),
                (message.get("code") or {}).get("code"),
                level,
                message.get("message", ""),
                root,
            )
        )
    return found


# -- text formats ------------------------------------------------------------------------

MYPY = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?:(?P<col>\d+):)? "
    r"(?P<level>error|warning|note): (?P<msg>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$"
)
PAREN = re.compile(
    r"^(?P<file>\S[^()]*?)\((?P<line>\d+)(?:,(?P<col>\d+))?\): "
    r"(?P<level>error|warning)\s*(?P<code>[A-Z]+\d+)?:?\s*(?P<msg>.*?)(?:\s+\[[^\]]+\])?$"
)
COLON = re.compile(
    r"^(?:vet: )?(?P<file>[^\s:()]+\.\w+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>\S.*)$"
)
TRAILING_RULE = re.compile(r"^(?P<msg>.*\S)\s+\((?P<rule>[\w-]+)\)$")
MAVEN = re.compile(
    r"^\[(?P<level>ERROR|WARNING)\]\s+(?P<file>\S+\.\w+):\[(?P<line>\d+),(?P<col>\d+)\]\s*(?P<msg>.*)$"
)


def parse_mypy(text: str, root: str | None = None) -> list[Diagnostic]:
    """mypy's one-line-per-message output (``--show-column-numbers --show-error-codes``)."""

    found = []
    for line in strip_ansi(text).splitlines():
        match = MYPY.match(line.strip())
        if not match or match["level"] == "note":
            continue
        severity: DiagnosticSeverity = "error" if match["level"] == "error" else "warning"
        found.append(
            _item(
                match["file"],
                int(match["line"]),
                int(match["col"]) if match["col"] else None,
                match["code"],
                severity,
                match["msg"],
                root,
            )
        )
    return found


def parse_paren_style(text: str, root: str | None = None) -> list[Diagnostic]:
    """``file(line,col): error CODE: message`` - tsc (``--pretty false``) and MSBuild/dotnet."""

    found = []
    for line in strip_ansi(text).splitlines():
        match = PAREN.match(line.strip())
        if match:
            severity: DiagnosticSeverity = "error" if match["level"] == "error" else "warning"
            found.append(
                _item(
                    match["file"],
                    int(match["line"]),
                    int(match["col"]) if match["col"] else None,
                    match["code"],
                    severity,
                    match["msg"],
                    root,
                )
            )
    return found


def parse_colon_style(text: str, root: str | None = None) -> list[Diagnostic]:
    """``file:line:col: message`` - go vet and build, golangci-lint, gcc-like tools. A trailing
    ``(rule)`` becomes the rule; messages that say ``warning`` are warnings."""

    found = []
    for line in strip_ansi(text).splitlines():
        match = COLON.match(line.strip())
        if not match:
            continue
        message, rule = match["msg"], None
        if trailing := TRAILING_RULE.match(message):
            message, rule = trailing["msg"], trailing["rule"]
        severity: DiagnosticSeverity = (
            "warning" if message.lower().startswith("warning") else "error"
        )
        found.append(
            _item(
                match["file"],
                int(match["line"]),
                int(match["col"]) if match["col"] else None,
                rule,
                severity,
                message,
                root,
            )
        )
    return found


def parse_maven(text: str, root: str | None = None) -> list[Diagnostic]:
    """``[ERROR] /path/File.java:[12,5] message`` lines from a Maven compile."""

    found = []
    for line in strip_ansi(text).splitlines():
        match = MAVEN.match(line.strip())
        if match:
            severity: DiagnosticSeverity = "error" if match["level"] == "ERROR" else "warning"
            found.append(
                _item(
                    match["file"],
                    int(match["line"]),
                    int(match["col"]),
                    None,
                    severity,
                    match["msg"],
                    root,
                )
            )
    return found


def sort_and_cap(items: list[Diagnostic], limit: int) -> tuple[list[Diagnostic], int]:
    """Errors first, then by file and line; at most ``limit`` kept, the rest only counted."""

    ordered = sorted(
        items, key=lambda d: (SEVERITY_ORDER[d.severity], d.file or "", d.line or 0, d.col or 0)
    )
    return ordered[:limit], max(len(ordered) - limit, 0)
