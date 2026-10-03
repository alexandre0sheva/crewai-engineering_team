"""Telling "the tool is not installed" apart from "the tool ran and failed", with install hints.

A missing tool must never look like a pass, so the runners return ``unavailable`` with a hint.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from engineering_team.devtools.detect import Stack

PYTHON_TOOLS = {"pytest", "ruff", "mypy", "black", "pyright", "pip-audit", "coverage", "pip_audit"}
JS_TOOLS = {"jest", "vitest", "eslint", "prettier", "tsc", "typescript"}
OTHER_HINTS = {
    "npx": "Install Node.js (it provides npm and npx).",
    "npm": "Install Node.js (it provides npm and npx).",
    "node": "Install Node.js.",
    "go": "Install Go (https://go.dev/dl/).",
    "cargo": "Install Rust with rustup (https://rustup.rs).",
    "mvn": "Install Apache Maven, or add the Maven wrapper (mvnw) to the project.",
    "gradle": "Install Gradle, or add the Gradle wrapper (gradlew) to the project.",
    "dotnet": "Install the .NET SDK (https://dotnet.microsoft.com/download).",
    "bundle": "Install Ruby and Bundler (`gem install bundler`).",
    "ruby": "Install Ruby.",
    "php": "Install PHP.",
    "composer": "Install Composer (https://getcomposer.org).",
    "uv": "Install uv (https://docs.astral.sh/uv/).",
    "python": "Install Python 3.",
    "golangci-lint": "Install golangci-lint (https://golangci-lint.run/welcome/install/).",
    "clippy": "Run `rustup component add clippy`.",
    "rustfmt": "Run `rustup component add rustfmt`.",
    "cargo-llvm-cov": "Run `cargo install cargo-llvm-cov`.",
    "cargo-audit": "Run `cargo install cargo-audit`.",
    "audit": "Run `cargo install cargo-audit`.",
    "rubocop": "Add `rubocop` to the Gemfile and run `bundle install`.",
    "rspec": "Add `rspec` to the Gemfile and run `bundle install`.",
    "phpunit": "Run `composer require --dev phpunit/phpunit`.",
    "phpcs": "Run `composer require --dev squizlabs/php_codesniffer`.",
}

NOT_FOUND_PATTERNS = (
    re.compile(r"No module named '?(?P<tool>[\w.]+)'?"),
    re.compile(r"no such command: `(?P<tool>[\w-]+)`"),
    re.compile(r"(?P<tool>[\w.-]+): command not found"),
    re.compile(r'exec: "(?P<tool>[\w.-]+)": executable file not found'),  # Docker's message
    re.compile(r"could not determine executable to run", re.IGNORECASE),
    re.compile(r"npx canceled due to missing packages", re.IGNORECASE),
    re.compile(r"Could not find command[ \"]+(?P<tool>[\w-]+)"),
    re.compile(r"No executable found matching command \"(?P<tool>[\w-]+)\""),
    re.compile(r"Cannot find module '(?P<tool>(?:jest|vitest|eslint|prettier|typescript)[^']*)'"),
    re.compile(r"cannot find the (?:path|file) specified", re.IGNORECASE),
)


def missing_tool(argv: Sequence[str], text: str, exit_code: int | None) -> str | None:
    """The tool that is not installed, if the output says so; else ``None``.

    Only the tool this command was meant to run counts: a project's own ``No module named
    'flask'`` or a script that calls a missing program is the project's failure, not ours.
    """

    if exit_code in (0, None):
        return None
    names = _tool_names(argv)
    for pattern in NOT_FOUND_PATTERNS:
        if not (match := pattern.search(text)):
            continue
        found = match.groupdict().get("tool")
        if found is None:  # patterns that name no tool (npx's message): the launcher is the tool
            if argv and argv[0] == "npx":
                return sorted(names - {"npx"})[0] if names - {"npx"} else "npx"
            continue
        if _norm(found) in {_norm(name) for name in names}:
            return _norm(found)
    return None


def _norm(name: str) -> str:
    return name.split(".")[0].replace("_", "-").lower()


def _tool_names(argv: Sequence[str]) -> set[str]:
    """Every name the command could report as missing: its program, module, or subcommand."""

    names = {Path(argv[0]).name} if argv else set()
    for index, token in enumerate(argv[:-1]):
        if token in ("-m", "exec"):
            names.add(argv[index + 1])
    if argv[:2] == ["npx", "--no-install"] and len(argv) > 2:
        names.add(argv[2])
    if argv[:1] and argv[0] in ("cargo", "dotnet") and len(argv) > 1:
        names.add(argv[1])
    return names


def install_hint(tool: str, stack: Stack | None) -> str:
    """How to get ``tool`` into the project, phrased for the stack's package manager."""

    name = tool.replace("_", "-")
    manager = stack.manager if stack else "pip"
    if name in PYTHON_TOOLS:
        if manager == "uv":
            return (
                f"Install it with `uv add --dev {name}` "
                "(or Install Dependencies if it is already declared)."
            )
        if manager == "poetry":
            return f"Install it with `poetry add --group dev {name}`."
        return (
            f"Install it with `python -m pip install {name}` "
            "(or add it to requirements.txt and run Install Dependencies)."
        )
    if name in JS_TOOLS:
        package = "typescript" if name == "tsc" else name
        command = {
            "npm": "npm install --save-dev",
            "pnpm": "pnpm add -D",
            "yarn": "yarn add -D",
            "bun": "bun add -d",
        }
        return f"Install it with `{command.get(manager, 'npm install --save-dev')} {package}`."
    return OTHER_HINTS.get(name, f"Install {name} and make sure it is on PATH.")
