"""Group ``code_intel``, language-server tools: Diagnostics and Go To Definition, plus the
precise half of Find References. They exist only when ``lsp.enabled`` is set (``docs/TOOLS.md``)."""

from __future__ import annotations

import contextlib
from collections import Counter
from pathlib import Path

from crewai.tools import BaseTool, tool

from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.lsp import (
    Diagnostic,
    Location,
    LspClient,
    LspError,
    LspUnavailable,
    from_utf16,
    to_utf16,
)
from engineering_team.codeintel.lsp_servers import language_of as server_language
from engineering_team.codeintel.references import word_pattern
from engineering_team.tools.support import ToolEnv, ToolError, bounded

MAX_LINE_CHARS = 200
MAX_MESSAGE_CHARS = 300
MAX_FILE_BYTES = 2_000_000
SEVERITY_ORDER = {"error": 0, "warning": 1, "information": 2, "hint": 3}


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"{path.name} is larger than {MAX_FILE_BYTES // 1_000_000} MB.")
    return path.read_text(encoding="utf-8", errors="replace")


def _line_text(path: Path | None, line: int) -> str:
    if path is None:
        return ""
    try:
        lines = _read(path).splitlines()
    except (OSError, ToolError):
        return ""
    return lines[line].strip()[:MAX_LINE_CHARS] if 0 <= line < len(lines) else ""


def _column(text: str, symbol: str, column: int) -> int:
    """The zero-based character column to ask about: ``column`` (1-based) or where ``symbol``
    first occurs as a whole word on the line."""

    if column > 0:
        if column > len(text) + 1:
            raise ToolError(f"The line has {len(text)} characters; column {column} is past it.")
        return column - 1
    if not symbol:
        raise ToolError("Give the symbol (a name on that line) or a column.")
    match = word_pattern(symbol).search(text)
    if match is None:
        raise ToolError(f"{symbol!r} does not occur on that line: {text.strip()[:120]!r}")
    return match.start()


class Lsp:
    """What the tools of one run need to ask a server about a file."""

    def __init__(self, env: ToolEnv) -> None:
        self.env = env

    def relative(self, path: Path) -> str:
        try:
            return self.env.workspace.relative_name(path)
        except ValueError:
            return str(path)  # outside the project (a library the symbol comes from)

    def session(self, path: Path) -> tuple[LspClient, str, str]:
        """``(server, uri, text)`` for ``path`` after telling the server its current text."""

        manager = self.env.ctx.lsp
        if manager is None or not manager.enabled:
            raise LspUnavailable("The language-server bridge is off (lsp.enabled).")
        client, language_id = manager.client_for(path)
        text = _read(path)
        return client, client.open(path, text, language_id), text

    def where(self, found: list[Location], limit: int) -> list[str]:
        lines = []
        for item in found[:limit]:
            text = _line_text(item.path, item.line)
            column = item.character
            if item.path is not None and text:
                with contextlib.suppress(OSError, ToolError, IndexError):
                    column = from_utf16(_read(item.path).splitlines()[item.line], item.character)
            name = self.relative(item.path) if item.path is not None else item.uri
            lines.append(f"{name}:{item.line + 1}:{column + 1}  {text}".rstrip())
        if len(found) > limit:
            lines.append(f"... {len(found) - limit} more; raise max_results.")
        return lines


def _problem(path: str, item: Diagnostic) -> str:
    message = " ".join(item.message.split())[:MAX_MESSAGE_CHARS]
    code = f" [{item.code}]" if item.code else ""
    return f"{path}:{item.line + 1}:{item.character + 1} {item.severity}{code} {message}"


def precise_references(
    env: ToolEnv, *, symbol: str, file: str, line: int, max_results: int
) -> tuple[str | None, str]:
    """References from a language server: ``(text, "")``, or ``(None, why)`` when none could
    answer, so Find References says why it fell back to word matching."""

    lsp = Lsp(env)
    try:
        path, row, text_line = _locate(env, symbol, file, line)
        client, uri, text = lsp.session(path)
        column = to_utf16(text_line, _column(text_line, symbol, 0))
        found = client.references(uri, row, column)
    except (LspError, ToolError, OSError) as exc:
        return None, str(exc)
    files = len({item.uri for item in found})
    head = (
        f"{len(found)} reference(s) to {symbol} in {files} file(s), from {client.name} "
        "(language server: exact, scope-aware)."
    )
    if not found:
        return head, ""
    return head + "\n" + "\n".join(lsp.where(found, max_results)), ""


def _locate(env: ToolEnv, symbol: str, file: str, line: int) -> tuple[Path, int, str]:
    """Where ``symbol`` is: the given file and line, or its one definition in the project."""

    workspace = env.workspace
    if file.strip() and line > 0:
        path = workspace.resolve(file, must_exist=True)
        lines = _read(path).splitlines()
        if line > len(lines):
            raise ToolError(f"{file} has {len(lines)} lines; line {line} is past the end.")
        return path, line - 1, lines[line - 1]
    index = SourceIndex.build(workspace)
    found = [
        (name, d)
        for name in index.files
        for d in index.definitions(name)
        if d.name == symbol and server_language(name) is not None
    ]
    if len(found) != 1:
        reason = "is not defined in the project" if not found else f"has {len(found)} definitions"
        raise ToolError(f"{symbol!r} {reason}; pass file and line of a use or the definition.")
    name, definition = found[0]
    path = workspace.resolve(name, must_exist=True)
    return path, definition.line - 1, _read(path).splitlines()[definition.line - 1]


def make_lsp_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace
    lsp = Lsp(env)

    @tool("Diagnostics")
    def diagnostics(path: str = ".", max_results: int = 60) -> str:
        """Compiler-grade errors and warnings from the language server (types, imports, syntax).

        path is a file, or a directory/project (the first lsp.max_diagnostic_files source files
        of it, sorted). Servers: pyright, typescript-language-server, gopls, rust-analyzer, when
        installed. Use it after editing, instead of guessing from test failures; Type Check runs
        the project's own checker. Example: path='src/app/models.py'.
        """

        def work() -> str:
            target = workspace.resolve(path, must_exist=True)
            limit = max(1, min(int(max_results), 300))
            if target.is_file():
                names = [workspace.relative_name(target)]
                skipped = 0
            else:
                prefix = workspace.relative_name(target)
                scope = "" if prefix == "." else prefix + "/"
                index = SourceIndex.build(workspace)
                every = sorted(
                    name
                    for name in index.files
                    if name.startswith(scope) and server_language(name) is not None
                )
                cap = env.ctx.settings.lsp.max_diagnostic_files
                names, skipped = every[:cap], max(0, len(every) - cap)
            if not names:
                return f"No file with a supported language server under {path}."
            found: list[tuple[str, Diagnostic]] = []
            server = ""
            failures: list[str] = []
            for name in names:
                try:
                    client, uri, _ = lsp.session(workspace.resolve(name, must_exist=True))
                    server = client.name
                    found.extend((name, item) for item in client.diagnostics(uri))
                except LspUnavailable as exc:
                    if len(names) == 1 or not server:
                        return f"ERROR: {exc} Use Type Check for the project's own checker."
                    failures.append(f"{name}: {exc}")
                    break
                except (LspError, ToolError, OSError) as exc:
                    failures.append(f"{name}: {exc}")
            found.sort(key=lambda p: (SEVERITY_ORDER.get(p[1].severity, 9), p[0], p[1].line))
            counts = Counter(item.severity for _, item in found)
            summary = ", ".join(f"{n} {kind}(s)" for kind, n in sorted(counts.items())) or "none"
            head = f"Diagnostics from {server} for {len(names)} file(s): {summary}."
            if skipped:
                head += f" ({skipped} more file(s) not opened; raise lsp.max_diagnostic_files.)"
            body = [_problem(name, item) for name, item in found[:limit]]
            if len(found) > limit:
                body.append(f"... {len(found) - limit} more; narrow path or raise max_results.")
            body += [f"Not checked: {failure}" for failure in failures[:5]]
            return bounded("\n".join([head, *body]), hint="narrow path or lower max_results")

        return env.run("Diagnostics", work, arguments={"path": path, "max_results": max_results})

    @tool("Go To Definition")
    def go_to_definition(path: str, line: int, symbol: str = "", column: int = 0) -> str:
        """Jump from a use to where it is defined, resolved by the language server.

        path and line (1-based) locate the use; symbol is the name on that line (its first whole
        word is used), or give column (1-based) instead. Follows imports, inheritance and
        library code; can point outside the project. Prefer it to Find Symbol when a name is
        defined in several places. Example: path='app/api.py', line=42, symbol='load_config'.
        """

        def work() -> str:
            target = workspace.resolve(path, must_exist=True)
            if not target.is_file():
                raise ToolError(f"{path} is a directory; give a source file.")
            try:
                client, uri, text = lsp.session(target)
            except LspUnavailable as exc:
                return f"ERROR: {exc} Use Find Symbol for a name-based search."
            lines = text.splitlines()
            if not 1 <= line <= len(lines):
                raise ToolError(f"{path} has {len(lines)} lines; line {line} does not exist.")
            row = lines[line - 1]
            offset = to_utf16(row, _column(row, symbol, int(column)))
            try:
                found = client.definition(uri, line - 1, offset)
            except LspError as exc:
                return f"ERROR: {exc}"
            if not found:
                return (
                    f"{client.name} found no definition at {path}:{line}"
                    f" ({symbol or 'column ' + str(column)}). Try Find Symbol."
                )
            return bounded("\n".join([f"Definition(s) from {client.name}:", *lsp.where(found, 20)]))

        return env.run(
            "Go To Definition",
            work,
            arguments={"path": path, "line": line, "symbol": symbol, "column": column},
        )

    return {"Diagnostics": diagnostics, "Go To Definition": go_to_definition}


__all__ = ["Lsp", "make_lsp_tools", "precise_references"]
