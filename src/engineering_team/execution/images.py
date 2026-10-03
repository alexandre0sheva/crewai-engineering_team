"""Which container image runs a command.

Chosen from the program being run first (``npm`` needs the Node image whatever the project
is), then from the detected stack of the project, and finally the Python image. An explicit
``execution.docker.image`` overrides all of it. Tags were checked against their registries
when this table was written; they are defaults, not promises, and every one is configurable.
"""

from __future__ import annotations

from pathlib import Path

from engineering_team.devtools.detect import find_stacks

# One tag per language family. The Python image is the official uv image (Python 3.12 and ``uv``
# on Debian slim) because the project's own dev tools run Python projects through ``uv``.
DEFAULT_IMAGES: dict[str, str] = {
    "python": "astral/uv:python3.12-bookworm-slim",
    "node": "node:22-slim",
    "go": "golang:1.25",
    "rust": "rust:1-slim",
    "java": "eclipse-temurin:21-jdk",
    "dotnet": "mcr.microsoft.com/dotnet/sdk:9.0",
    "ruby": "ruby:3.3-slim",
    "php": "php:8.3-cli",
}
DEFAULT_FAMILY = "python"

_PROGRAMS: dict[str, tuple[str, ...]] = {
    "python": (
        "python",
        "python3",
        "pip",
        "pip3",
        "uv",
        "uvx",
        "pytest",
        "ruff",
        "mypy",
        "black",
        "flake8",
        "pylint",
        "poetry",
        "pdm",
        "bandit",
        "pip-audit",
        "coverage",
        "isort",
    ),
    "node": (
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "corepack",
        "tsc",
        "eslint",
        "prettier",
        "vitest",
        "jest",
        "playwright",
    ),
    "go": ("go", "gofmt", "gosec", "golangci-lint", "govulncheck"),
    "rust": ("cargo", "rustc", "rustfmt", "clippy-driver", "cargo-audit"),
    "java": ("java", "javac", "mvn", "gradle", "jar"),
    "dotnet": ("dotnet",),
    "ruby": ("ruby", "bundle", "bundler", "gem", "rake", "rspec", "rubocop"),
    "php": ("php", "composer", "phpunit"),
}
PROGRAM_FAMILY = {program: family for family, names in _PROGRAMS.items() for program in names}

_LANGUAGE_FAMILY = {
    "python": "python",
    "javascript": "node",
    "typescript": "node",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "dotnet",
    "ruby": "ruby",
    "php": "php",
}


def family_for(program: str, root: Path, cwd: Path) -> str:
    """The language family for ``program`` run in ``cwd`` of the project at ``root``."""

    name = Path(program).name.removesuffix(".exe").lower()
    if name in PROGRAM_FAMILY:
        return PROGRAM_FAMILY[name]
    try:
        stacks = find_stacks(root)
        relative = cwd.relative_to(root).as_posix()
    except (OSError, ValueError):
        return DEFAULT_FAMILY
    # The stack whose directory contains ``cwd`` (deepest first), else the project's first.
    inside = [
        stack
        for stack in stacks
        if stack.directory == "."
        or relative == stack.directory
        or relative.startswith(f"{stack.directory}/")
    ]
    chosen = max(inside, key=lambda stack: len(stack.directory), default=None) or next(
        iter(stacks), None
    )
    return _LANGUAGE_FAMILY.get(chosen.language, DEFAULT_FAMILY) if chosen else DEFAULT_FAMILY


def default_image(family: str) -> str:
    return DEFAULT_IMAGES.get(family, DEFAULT_IMAGES[DEFAULT_FAMILY])
