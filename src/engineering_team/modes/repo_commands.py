"""Which commands an existing project implies, read from its own files and never run here.

Two sources, project scripts first: what the project defines for itself (``package.json``
scripts, ``Makefile`` and ``justfile`` targets, ``tox.ini``, ``noxfile.py``) and what the shared
stack detector (``devtools.detect``, T11) says about the tools it uses. Several commands of one
kind are kept, the most authoritative first, so a consumer takes the first of its kind.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import Path

from engineering_team.devtools.detect import Stack
from engineering_team.modes.repo_profile import CommandKind, DetectedCommand
from engineering_team.tools.scripts import Script, discover_scripts
from engineering_team.tools.support import ToolError

KINDS: tuple[CommandKind, ...] = ("setup", "test", "lint", "typecheck", "format", "build", "run")

# Script and target names that mean a kind of command. ``format`` alone is not here: it usually
# rewrites files, and a detected command must be safe to suggest running.
SCRIPT_KINDS: dict[CommandKind, tuple[str, ...]] = {
    "test": ("test", "tests", "test:unit", "unit", "test:ci", "check"),
    "lint": ("lint", "lint:check", "eslint", "check:lint"),
    "typecheck": ("typecheck", "type-check", "check-types", "check:types", "tsc", "mypy"),
    "format": ("format:check", "check-format", "fmt:check", "format-check", "prettier:check"),
    "build": ("build", "compile", "package"),
    "run": ("start", "dev", "serve", "run"),
}


def detect_commands(root: Path, stacks: Iterable[Stack]) -> tuple[list[DetectedCommand], list[str]]:
    """``(commands, notes)`` for the project directories ``stacks`` and the root."""

    commands: list[DetectedCommand] = []
    notes: list[str] = []
    directories = list(dict.fromkeys([".", *(stack.directory for stack in stacks)]))
    by_directory = {stack.directory: stack for stack in stacks}
    for directory in directories:
        base = root if directory == "." else root / directory
        found: list[DetectedCommand] = []
        try:
            found += _project_scripts(base, directory)
        except ToolError as exc:
            notes.append(f"{directory}: {exc}")
        found += _runner_files(base, directory)
        if stack := by_directory.get(directory):
            found += _from_stack(stack, base)
        commands.extend(_ordered(found))
    return commands, notes


def _ordered(found: list[DetectedCommand]) -> list[DetectedCommand]:
    """By kind, keeping each kind's order of discovery and dropping repeated command lines."""

    seen: set[tuple[str, str, str]] = set()
    ordered: list[DetectedCommand] = []
    for kind in KINDS:
        for command in found:
            key = (command.kind, command.directory, command.command)
            if command.kind == kind and key not in seen:
                seen.add(key)
                ordered.append(command)
    return ordered


def _make(kind: CommandKind, directory: str, argv: Iterable[str], source: str) -> DetectedCommand:
    parts = list(argv)
    return DetectedCommand(
        kind=kind, directory=directory, command=shlex.join(parts), argv=parts, source=source
    )


# -- what the project defines for itself --------------------------------------------------


def _project_scripts(base: Path, directory: str) -> list[DetectedCommand]:
    found: list[DetectedCommand] = []
    scripts = discover_scripts(base)
    for kind, names in SCRIPT_KINDS.items():
        for name in names:
            for script in _scripts_named(scripts, name):
                found.append(_from_script(kind, directory, script))
    return found


def _scripts_named(scripts: list[Script], name: str) -> list[Script]:
    # A pyproject console script is an entry point, not a build step; ``uv run <name>`` only
    # means something for ``run``, so it is not offered for the other kinds.
    return [s for s in scripts if s.name == name and s.source != "uv"]


def _from_script(kind: CommandKind, directory: str, script: Script) -> DetectedCommand:
    labels = {
        "npm": "package.json script", "pnpm": "package.json script",
        "yarn": "package.json script", "bun": "package.json script",
        "make": "Makefile target", "just": "justfile recipe",
    }  # fmt: skip
    argv = list(script.argv)
    if script.source in ("npm", "pnpm", "yarn") and script.name == "test":
        argv = [script.source, "test"]  # `npm test` is how everyone runs it
    return _make(
        kind, directory, argv, f"{labels.get(script.source, script.source)} '{script.name}'"
    )


def _runner_files(base: Path, directory: str) -> list[DetectedCommand]:
    found: list[DetectedCommand] = []
    if (base / "tox.ini").is_file():
        found.append(_make("test", directory, ["tox"], "tox.ini"))
    if (base / "noxfile.py").is_file():
        found.append(_make("test", directory, ["nox"], "noxfile.py"))
    return found


# -- what the stack's tools imply ---------------------------------------------------------


def _from_stack(stack: Stack, base: Path) -> list[DetectedCommand]:
    found: list[DetectedCommand] = []
    d = stack.directory

    def add(kind: CommandKind, argv: Iterable[str]) -> None:
        found.append(_make(kind, d, argv, f"detected: {stack.language} ({stack.manager})"))

    if setup := _setup(stack, base):
        add("setup", setup)
    for kind, tool in (
        ("test", stack.test),
        ("lint", stack.lint),
        ("typecheck", stack.typecheck),
        ("format", stack.format),
    ):
        if tool and (argv := TOOL_COMMANDS.get((kind, tool))):
            add(kind, _wrap(stack, argv))  # type: ignore[arg-type]
    if stack.build:
        add("build", stack.build)
    return found


def _wrap(stack: Stack, argv: tuple[str, ...]) -> list[str]:
    """``argv`` run the way the project's own package manager runs tools."""

    program = argv[0]
    if stack.language == "python":
        prefix = {"uv": ["uv", "run"], "poetry": ["poetry", "run"], "pipenv": ["pipenv", "run"]}
        if stack.manager in prefix:
            return [*prefix[stack.manager], *argv]
        return list(argv) if program == "python" else ["python", "-m", *argv]
    if stack.language in ("javascript", "typescript") and program in JS_TOOLS:
        runner = {"npm": ["npx"], "pnpm": ["pnpm", "exec"], "yarn": ["yarn"], "bun": ["bunx"]}
        return [*runner.get(stack.manager, ["npx"]), *argv]
    if stack.language == "ruby" and program != "bundle":
        return ["bundle", "exec", *argv]
    return list(argv)


JS_TOOLS = frozenset({"jest", "vitest", "eslint", "tsc", "prettier"})

# (kind, the detector's tool name) -> the command that tool implies.
TOOL_COMMANDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("test", "pytest"): ("pytest",),
    ("test", "unittest"): ("python", "-m", "unittest", "discover"),
    ("test", "jest"): ("jest",),
    ("test", "vitest"): ("vitest", "run"),
    ("test", "go"): ("go", "test", "./..."),
    ("test", "cargo"): ("cargo", "test"),
    ("test", "maven"): ("mvn", "-B", "test"),
    ("test", "gradle"): ("gradle", "test"),
    ("test", "dotnet"): ("dotnet", "test"),
    ("test", "rspec"): ("rspec",),
    ("test", "minitest"): ("rake", "test"),
    ("test", "phpunit"): ("vendor/bin/phpunit",),
    ("lint", "ruff"): ("ruff", "check", "."),
    ("lint", "eslint"): ("eslint", "."),
    ("lint", "golangci-lint"): ("golangci-lint", "run"),
    ("lint", "clippy"): ("cargo", "clippy"),
    ("lint", "rubocop"): ("rubocop",),
    ("lint", "phpcs"): ("vendor/bin/phpcs",),
    ("typecheck", "mypy"): ("mypy", "."),
    ("typecheck", "pyright"): ("pyright",),
    ("typecheck", "tsc"): ("tsc", "--noEmit"),
    ("typecheck", "go-vet"): ("go", "vet", "./..."),
    ("typecheck", "cargo-check"): ("cargo", "check"),
    ("format", "ruff"): ("ruff", "format", "--check", "."),
    ("format", "black"): ("black", "--check", "."),
    ("format", "prettier"): ("prettier", "--check", "."),
    ("format", "gofmt"): ("gofmt", "-l", "."),
    ("format", "rustfmt"): ("cargo", "fmt", "--check"),
}


def _setup(stack: Stack, base: Path) -> list[str] | None:
    manager = stack.manager
    if manager == "uv":
        return ["uv", "sync"]
    if manager == "poetry":
        return ["poetry", "install"]
    if manager == "pipenv":
        return ["pipenv", "install", "--dev"]
    if manager == "pip":
        has_requirements = (base / "requirements.txt").is_file()
        return (
            ["python", "-m", "pip", "install", "-r", "requirements.txt"]
            if has_requirements
            else None
        )
    if manager == "npm":
        return ["npm", "ci"] if (base / "package-lock.json").is_file() else ["npm", "install"]
    if manager in ("pnpm", "yarn", "bun"):
        return [manager, "install"]
    return {
        "go": ["go", "mod", "download"],
        "cargo": ["cargo", "fetch"],
        "dotnet": ["dotnet", "restore"],
        "bundler": ["bundle", "install"],
        "composer": ["composer", "install"],
    }.get(manager)
