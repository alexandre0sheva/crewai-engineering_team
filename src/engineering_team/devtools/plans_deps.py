"""Command lines for coverage, dependency installation, and dependency audits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engineering_team.devtools.detect import Stack
from engineering_team.devtools.models import FileCoverage, Vulnerability
from engineering_team.devtools.parsers import audit, coverage
from engineering_team.devtools.parsers.common import json_text
from engineering_team.devtools.plans import (
    Paths,
    Selection,
    TestCommand,
    build_test_command,
    go_module,
    python_argv,
)
from engineering_team.tools.support import ToolError


@dataclass
class CoverageCommand:
    tests: TestCommand
    follow_up: list[str] | None  # a second command that turns the data into a report
    report_file: Path
    parse: Callable[[str], list[FileCoverage]]
    tool: str


def coverage_command(
    stack: Stack, directory: Path, selection: Selection, paths: Paths, root: str
) -> CoverageCommand:
    framework = stack.test
    data = paths.directory / "coverage"
    if stack.language == "python" and framework in ("pytest", "unittest"):
        inner = build_test_command(stack, directory, selection, paths)
        runner = python_argv(directory, "coverage", module=True)
        data_file = paths.directory / ".coverage"
        inner.argv = [*runner, "run", f"--data-file={data_file}", "-m", *_module_form(inner.argv)]
        report = paths.directory / "coverage.json"
        follow = [*runner, "json", f"--data-file={data_file}", "-o", str(report)]
        return CoverageCommand(
            inner, follow, report, lambda t: coverage.parse_coverage_py_json(t, root), "coverage.py"
        )
    if framework in ("jest", "vitest"):
        inner = build_test_command(stack, directory, selection, paths)
        flag = (
            ["--coverage", "--coverage.reporter=lcov", f"--coverage.reportsDirectory={data}"]
            if framework == "vitest"
            else ["--coverage", "--coverageReporters=lcov", f"--coverageDirectory={data}"]
        )
        inner.argv = [*inner.argv[:4], *flag, *inner.argv[4:]]
        return CoverageCommand(
            inner, None, data / "lcov.info", lambda t: coverage.parse_lcov(t, root), str(framework)
        )
    if framework == "go":
        inner = build_test_command(stack, directory, selection, paths)
        profile = paths.directory / "go.cover"
        inner.argv = [*inner.argv[:4], f"-coverprofile={profile}", *inner.argv[4:]]
        module = go_module(directory)
        return CoverageCommand(
            inner, None, profile, lambda t: coverage.parse_go_cover(t, module), "go cover"
        )
    if framework == "cargo":
        inner = build_test_command(stack, directory, selection, paths)
        report = paths.directory / "lcov.info"
        inner.argv = ["cargo", "llvm-cov", "--lcov", "--output-path", str(report), *inner.argv[2:]]
        return CoverageCommand(
            inner, None, report, lambda t: coverage.parse_lcov(t, root), "cargo-llvm-cov"
        )
    raise ToolError(
        f"Coverage is supported for pytest/unittest (coverage.py), jest/vitest, go, and cargo "
        f"(cargo-llvm-cov); {stack.directory!r} uses {framework or 'no detected test framework'}. "
        "Run the project's own coverage command with Run Project Command."
    )


def _module_form(argv: list[str]) -> list[str]:
    """``[python, -m, pytest, ...]`` -> ``[pytest, ...]``: what ``coverage run -m`` takes."""

    if "-m" in argv:
        return argv[argv.index("-m") + 1 :]
    return argv


INSTALL_COMMANDS: dict[str, list[str]] = {
    "uv": ["uv", "sync"],
    "poetry": ["poetry", "install"],
    "pipenv": ["pipenv", "install", "--dev"],
    "npm": ["npm", "install"],
    "pnpm": ["pnpm", "install"],
    "yarn": ["yarn", "install"],
    "bun": ["bun", "install"],
    "go": ["go", "mod", "download"],
    "cargo": ["cargo", "fetch"],
    "maven": ["mvn", "-B", "dependency:resolve"],
    "gradle": ["gradle", "dependencies"],
    "dotnet": ["dotnet", "restore"],
    "bundler": ["bundle", "install"],
    "composer": ["composer", "install"],
}


def install_argv(stack: Stack, directory: Path) -> list[str]:
    """The command that installs a stack's declared dependencies, preferring lockfiles."""

    manager = stack.manager
    if manager == "npm" and (directory / "package-lock.json").exists():
        return ["npm", "ci"]
    if manager == "pnpm" and (directory / "pnpm-lock.yaml").exists():
        return ["pnpm", "install", "--frozen-lockfile"]
    if manager == "yarn" and (directory / "yarn.lock").exists():
        return ["yarn", "install", "--frozen-lockfile"]
    if manager == "pip":
        python = python_argv(directory, "pip", module=True)
        if (directory / "requirements.txt").exists():
            return [*python, "install", "-r", "requirements.txt"]
        if (directory / "pyproject.toml").exists() or (directory / "setup.py").exists():
            return [*python, "install", "-e", "."]
        raise ToolError(
            "A Python project with no requirements.txt, pyproject.toml, or setup.py declares "
            "no dependencies to install."
        )
    command = INSTALL_COMMANDS.get(manager)
    if command is None:
        raise ToolError(f"Do not know how to install dependencies for {manager!r}.")
    return list(command)


@dataclass
class AuditCommand:
    argv: list[str]
    tool: str
    parse: Callable[[str], list[Vulnerability]]


def audit_command(stack: Stack, directory: Path) -> AuditCommand:
    if stack.language == "python":
        return AuditCommand(
            [*python_argv(directory, "pip-audit"), "-f", "json"],
            "pip-audit",
            lambda t: audit.parse_pip_audit(json_text(t)),
        )
    if stack.manager in ("npm", "pnpm"):
        return AuditCommand(
            [stack.manager, "audit", "--json"],
            f"{stack.manager} audit",
            lambda t: audit.parse_npm_audit(json_text(t)),
        )
    if stack.manager == "cargo":
        return AuditCommand(
            ["cargo", "audit", "--json"],
            "cargo audit",
            lambda t: audit.parse_cargo_audit(json_text(t)),
        )
    raise ToolError(
        f"Dependency Audit supports pip-audit, npm/pnpm audit, and cargo audit; "
        f"{stack.directory!r} uses {stack.manager}."
    )
