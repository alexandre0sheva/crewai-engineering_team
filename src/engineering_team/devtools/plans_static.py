"""Command lines for the linters, type checkers, formatters, and builds."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from engineering_team.devtools.detect import Stack
from engineering_team.devtools.models import Diagnostic, DiagnosticKind
from engineering_team.devtools.parsers import diagnostics
from engineering_team.devtools.parsers import format as formatting
from engineering_team.devtools.parsers.common import json_text
from engineering_team.devtools.plans import (
    SUPPORTED_FORMATTERS,
    SUPPORTED_LINTERS,
    SUPPORTED_TYPECHECKERS,
    local_executable,
    python_argv,
)
from engineering_team.tools.support import ToolError


@dataclass
class DiagnosticCommand:
    argv: list[str]
    tool: str
    kind: DiagnosticKind
    parse: Callable[[str], list[Diagnostic]]


def _targets(sel: Sequence[str], default: Sequence[str] = ()) -> list[str]:
    return list(sel) or list(default)


def lint_command(
    tool: str, directory: Path, targets: Sequence[str], root: str
) -> DiagnosticCommand:
    if tool == "ruff":
        argv = [*python_argv(directory, "ruff"), "check", "--output-format", "json", "--no-fix"]
        return DiagnosticCommand(
            [*argv, *_targets(targets, ["."])],
            tool,
            "lint",
            lambda t: diagnostics.parse_ruff(json_text(t), root),
        )
    if tool == "eslint":
        argv = ["npx", "--no-install", "eslint", "-f", "json", *_targets(targets, ["."])]
        return DiagnosticCommand(
            argv, tool, "lint", lambda t: diagnostics.parse_eslint(json_text(t), root)
        )
    if tool == "golangci-lint":
        argv = ["golangci-lint", "run", *_targets(targets, ["./..."])]
        return DiagnosticCommand(
            argv, tool, "lint", lambda t: diagnostics.parse_colon_style(t, root)
        )
    if tool == "clippy":
        argv = ["cargo", "clippy", "--message-format=json", "--all-targets"]
        return DiagnosticCommand(
            argv, tool, "lint", lambda t: diagnostics.parse_cargo_json(t, root)
        )
    if tool == "rubocop":
        argv = ["rubocop", "--format", "json", *targets]
        return DiagnosticCommand(
            argv, tool, "lint", lambda t: diagnostics.parse_rubocop(json_text(t), root)
        )
    if tool == "phpcs":
        exe = local_executable(directory, "vendor/bin/phpcs", "phpcs")
        argv = [exe, "--report=json", *_targets(targets, ["."])]
        return DiagnosticCommand(
            argv, tool, "lint", lambda t: diagnostics.parse_phpcs(json_text(t), root)
        )
    raise ToolError(f"Unknown linter {tool!r}. Supported: {', '.join(SUPPORTED_LINTERS)}.")


def typecheck_command(
    tool: str, directory: Path, targets: Sequence[str], root: str
) -> DiagnosticCommand:
    if tool == "mypy":
        argv = [
            *python_argv(directory, "mypy"),
            "--show-column-numbers",
            "--show-error-codes",
            "--no-error-summary",
            "--no-pretty",
            "--no-color-output",
            *_targets(targets, ["."]),
        ]
        return DiagnosticCommand(argv, tool, "typecheck", lambda t: diagnostics.parse_mypy(t, root))
    if tool == "pyright":
        argv = ["pyright", "--outputjson", *targets]
        return DiagnosticCommand(
            argv, tool, "typecheck", lambda t: diagnostics.parse_pyright(json_text(t), root)
        )
    if tool == "tsc":
        argv = ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"]
        return DiagnosticCommand(
            argv, tool, "typecheck", lambda t: diagnostics.parse_paren_style(t, root)
        )
    if tool == "go-vet":
        argv = ["go", "vet", *_targets(targets, ["./..."])]
        return DiagnosticCommand(
            argv, tool, "typecheck", lambda t: diagnostics.parse_colon_style(t, root)
        )
    if tool == "cargo-check":
        argv = ["cargo", "check", "--message-format=json", "--all-targets"]
        return DiagnosticCommand(
            argv, tool, "typecheck", lambda t: diagnostics.parse_cargo_json(t, root)
        )
    raise ToolError(
        f"Unknown type checker {tool!r}. Supported: {', '.join(SUPPORTED_TYPECHECKERS)}."
    )


def build_parser(stack: Stack, root: str) -> Callable[[str], list[Diagnostic]]:
    """How to read the build errors of a stack's build command."""

    if stack.language == "rust":
        return lambda text: diagnostics.parse_cargo_json(text, root)
    if stack.language == "go":
        return lambda text: diagnostics.parse_colon_style(text, root)
    if stack.language in ("csharp", "typescript"):
        return lambda text: diagnostics.parse_paren_style(text, root)
    if stack.manager == "maven":
        return lambda text: diagnostics.parse_maven(text, root)
    return lambda text: diagnostics.parse_colon_style(text, root)


def build_argv(stack: Stack) -> list[str]:
    if stack.build is None:
        raise ToolError(
            f"No build command detected in {stack.directory!r}. Use List Scripts and Run Script, "
            "or Run Project Command with the build command."
        )
    argv = list(stack.build)
    if stack.language == "rust":
        argv.append("--message-format=json")
    return argv


@dataclass
class FormatCommand:
    argv: list[str]
    tool: str
    parse: Callable[[str], list[str]]


def format_command(
    tool: str, directory: Path, targets: Sequence[str], *, write: bool, root: str
) -> FormatCommand:
    def listing(text: str) -> list[str]:
        return formatting.parse_format_check(tool, text, root)

    if tool == "ruff":
        argv = [*python_argv(directory, "ruff"), "format", *([] if write else ["--check"])]
        return FormatCommand([*argv, *_targets(targets, ["."])], tool, listing)
    if tool == "black":
        argv = [*python_argv(directory, "black"), *([] if write else ["--check"])]
        return FormatCommand([*argv, *_targets(targets, ["."])], tool, listing)
    if tool == "prettier":
        argv = ["npx", "--no-install", "prettier", "--write" if write else "--check"]
        return FormatCommand([*argv, *_targets(targets, ["."])], tool, listing)
    if tool == "gofmt":
        argv = ["gofmt", "-w" if write else "-l", *_targets(targets, ["."])]
        return FormatCommand(argv, tool, listing)
    if tool == "rustfmt":
        argv = ["cargo", "fmt"] if write else ["cargo", "fmt", "--", "--check"]
        return FormatCommand(argv, tool, listing)
    raise ToolError(f"Unknown formatter {tool!r}. Supported: {', '.join(SUPPORTED_FORMATTERS)}.")
