"""Stack and tool detection: what a directory is, and which tools it uses.

One implementation shared by the developer tools, the verification profiles, and the
repository analyzer. Detection reads manifests and config files only; it never runs a
tool, so a detected tool may still be missing (the runners report that as ``unavailable``).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engineering_team.tools.workspace import IGNORED_LIST_DIRECTORIES

MAX_FILE_BYTES = 500_000


@dataclass(frozen=True)
class Stack:
    """One project directory and the tools it uses. ``None`` means none was detected."""

    directory: str  # relative to the workspace root; "." for the root
    language: str
    manager: str
    test: str | None = None
    lint: str | None = None
    typecheck: str | None = None
    format: str | None = None
    coverage: str | None = None
    audit: str | None = None
    build: tuple[str, ...] | None = None
    notes: tuple[str, ...] = field(default=())

    def describe(self) -> str:
        parts = [f"{self.language} ({self.manager})"]
        for label in ("test", "lint", "typecheck", "format"):
            if value := getattr(self, label):
                parts.append(f"{label} {value}")
        return ", ".join(parts)


def _read(path: Path) -> str:
    try:
        if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError:
        return {}


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(path))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _any(directory: Path, *names: str) -> bool:
    return any((directory / name).exists() for name in names)


def _mentions(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE) is not None


# -- per language ------------------------------------------------------------------------


def _python(directory: Path, relative: str) -> Stack | None:
    pyproject = _toml(directory / "pyproject.toml")
    manifests = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "tox.ini", "pytest.ini")
    requirements = sorted(directory.glob("requirements*.txt"))
    loose = any(directory.glob("*.py")) or (directory / "tests").is_dir()
    if not (_any(directory, *manifests) or requirements or loose):
        return None
    tool = pyproject.get("tool", {}) if isinstance(pyproject.get("tool"), dict) else {}
    text = "\n".join(
        _read(directory / name) for name in ("pyproject.toml", "setup.cfg", "tox.ini", "Pipfile")
    ) + "\n".join(_read(path) for path in requirements)

    if "uv" in tool or (directory / "uv.lock").exists():
        manager = "uv"
    elif "poetry" in tool or (directory / "poetry.lock").exists():
        manager = "poetry"
    elif (directory / "Pipfile").exists():
        manager = "pipenv"
    else:
        manager = "pip"

    test_files = [*directory.glob("test*.py"), *directory.glob("tests/**/test*.py")][:20]
    if (
        "pytest" in tool
        or _any(directory, "pytest.ini", "conftest.py")
        or _mentions(text, "pytest")
    ):
        test: str | None = "pytest"
    elif test_files:
        uses_unittest = any("unittest" in _read(path) for path in test_files)
        test = "unittest" if uses_unittest else "pytest"
    else:
        test = None

    ruff = "ruff" in tool or _any(directory, "ruff.toml", ".ruff.toml") or _mentions(text, "ruff")
    black = "black" in tool or _mentions(text, "black")
    if "mypy" in tool or _any(directory, "mypy.ini", ".mypy.ini") or _mentions(text, "mypy"):
        typecheck: str | None = "mypy"
    elif "pyright" in tool or _any(directory, "pyrightconfig.json") or _mentions(text, "pyright"):
        typecheck = "pyright"
    else:
        typecheck = None
    build = None
    if "build-system" in pyproject:
        build = ("uv", "build") if manager == "uv" else ("python", "-m", "build")
    return Stack(
        directory=relative,
        language="python",
        manager=manager,
        test=test,
        lint="ruff" if ruff else None,
        typecheck=typecheck,
        format="ruff" if ruff else "black" if black else None,
        coverage="coverage.py" if test else None,
        audit="pip-audit",
        build=build,
    )


def _javascript(directory: Path, relative: str) -> Stack | None:
    package = _json(directory / "package.json")
    if not (directory / "package.json").is_file():
        return None
    dependencies = {
        **(package.get("dependencies") or {}),
        **(package.get("devDependencies") or {}),
    }
    scripts = package.get("scripts") or {}
    manager = next(
        (
            name
            for lock, name in (
                ("pnpm-lock.yaml", "pnpm"),
                ("yarn.lock", "yarn"),
                ("bun.lock", "bun"),
                ("bun.lockb", "bun"),
            )
            if (directory / lock).exists()
        ),
        "npm",
    )
    typescript = (directory / "tsconfig.json").exists() or "typescript" in dependencies
    notes: tuple[str, ...] = ()
    if "vitest" in dependencies or _any(directory, "vitest.config.ts", "vitest.config.js"):
        test: str | None = "vitest"
    elif "jest" in dependencies or _any(directory, "jest.config.js", "jest.config.ts"):
        test = "jest"
    else:
        test = None
        if "test" in scripts:
            notes = (
                "package.json has a test script for an unrecognised framework; use Run Script",
            )
    eslint = (
        "eslint" in dependencies
        or any(directory.glob(".eslintrc*"))
        or any(directory.glob("eslint.config.*"))
    )
    prettier = (
        "prettier" in dependencies
        or any(directory.glob(".prettierrc*"))
        or any(directory.glob("prettier.config.*"))
    )
    return Stack(
        directory=relative,
        language="typescript" if typescript else "javascript",
        manager=manager,
        test=test,
        lint="eslint" if eslint else None,
        typecheck="tsc" if (directory / "tsconfig.json").exists() else None,
        format="prettier" if prettier else None,
        coverage=test,
        audit=manager if manager in ("npm", "pnpm") else None,
        build=(manager, "run", "build") if "build" in scripts else None,
        notes=notes,
    )


def _go(directory: Path, relative: str) -> Stack | None:
    if not (directory / "go.mod").is_file():
        return None
    return Stack(
        directory=relative,
        language="go",
        manager="go",
        test="go",
        lint="golangci-lint" if any(directory.glob(".golangci.*")) else None,
        typecheck="go-vet",
        format="gofmt",
        coverage="go",
        build=("go", "build", "./..."),
    )


def _rust(directory: Path, relative: str) -> Stack | None:
    if not (directory / "Cargo.toml").is_file():
        return None
    return Stack(
        directory=relative,
        language="rust",
        manager="cargo",
        test="cargo",
        lint="clippy",
        typecheck="cargo-check",
        format="rustfmt",
        coverage="cargo-llvm-cov",
        audit="cargo-audit",
        build=("cargo", "build"),
    )


def _java(directory: Path, relative: str) -> Stack | None:
    if (directory / "pom.xml").is_file():
        wrapper = "./mvnw" if (directory / "mvnw").exists() else "mvn"
        return Stack(
            directory=relative,
            language="java",
            manager="maven",
            test="maven",
            build=(wrapper, "-B", "compile"),
        )
    if _any(directory, "build.gradle", "build.gradle.kts"):
        wrapper = "./gradlew" if (directory / "gradlew").exists() else "gradle"
        return Stack(
            directory=relative,
            language="java",
            manager="gradle",
            test="gradle",
            build=(wrapper, "build", "-x", "test"),
        )
    return None


def _dotnet(directory: Path, relative: str) -> Stack | None:
    if not (any(directory.glob("*.sln")) or any(directory.glob("*.csproj"))):
        return None
    return Stack(
        directory=relative,
        language="csharp",
        manager="dotnet",
        test="dotnet",
        build=("dotnet", "build", "--nologo"),
    )


def _ruby(directory: Path, relative: str) -> Stack | None:
    gemfile = _read(directory / "Gemfile")
    if not (directory / "Gemfile").is_file():
        return None
    if _any(directory, ".rspec", "spec") or _mentions(gemfile, "rspec"):
        test: str | None = "rspec"
    elif (directory / "test").is_dir():
        test = "minitest"
    else:
        test = None
    rubocop = _any(directory, ".rubocop.yml") or _mentions(gemfile, "rubocop")
    return Stack(
        directory=relative,
        language="ruby",
        manager="bundler",
        test=test,
        lint="rubocop" if rubocop else None,
    )


def _php(directory: Path, relative: str) -> Stack | None:
    composer = _json(directory / "composer.json")
    if not (directory / "composer.json").is_file():
        return None
    dependencies = {**(composer.get("require") or {}), **(composer.get("require-dev") or {})}
    phpunit = "phpunit/phpunit" in dependencies or _any(
        directory, "phpunit.xml", "phpunit.xml.dist"
    )
    phpcs = "squizlabs/php_codesniffer" in dependencies or _any(
        directory, "phpcs.xml", "phpcs.xml.dist"
    )
    return Stack(
        directory=relative,
        language="php",
        manager="composer",
        test="phpunit" if phpunit else None,
        lint="phpcs" if phpcs else None,
    )


DETECTORS = (_python, _javascript, _go, _rust, _java, _dotnet, _ruby, _php)


def detect_stack(directory: Path, relative: str = ".") -> Stack | None:
    """The stack of ``directory`` itself (no recursion), or ``None`` if it has no manifest.

    When one directory holds several (a Python backend with a ``package.json`` for tooling) the
    first match in this order wins: Python, JavaScript/TypeScript, Go, Rust, Java, C#, Ruby, PHP.
    """

    for detector in DETECTORS:
        stack = detector(directory, relative)
        if stack is not None:
            return stack
    return None


def find_stacks(root: Path, *, depth: int = 2) -> list[Stack]:
    """Every stack at ``root`` or up to ``depth`` directories below it, in path order."""

    found: list[Stack] = []

    def walk(directory: Path, level: int) -> None:
        relative = directory.relative_to(root).as_posix()
        if stack := detect_stack(directory, relative):
            found.append(stack)
        if level >= depth:
            return
        try:
            children = sorted(entry for entry in directory.iterdir() if entry.is_dir())
        except OSError:
            return
        for child in children:
            if child.name not in IGNORED_LIST_DIRECTORIES and not child.name.startswith("."):
                walk(child, level + 1)

    walk(root, 0)
    return found
