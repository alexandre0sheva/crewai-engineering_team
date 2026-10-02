"""Group ``dev``: structured test, lint, type-check, format, build, coverage, install, audit.

Each tool runs its command through the execution backend (via ``DevRunner``) and returns a short
verdict with the few facts an agent needs: failing tests with file, line, and message instead of a
whole log. A tool that is not installed answers ``UNAVAILABLE`` with a hint, never a pass.
"""

from __future__ import annotations

from collections.abc import Callable

from crewai.tools import BaseTool, tool

from engineering_team.tools.support import ToolEnv, bounded


def _split(paths: str) -> list[str]:
    return [item.strip() for item in paths.replace("\n", ",").split(",") if item.strip()]


def make_dev_tools(env: ToolEnv) -> dict[str, BaseTool]:
    # Imported here because ``devtools`` imports the ``tools`` package (the command allowlist, the
    # ``ToolError`` type) while the ``tools`` package imports this module to register the tools.
    from engineering_team.devtools.render import (
        render_audit,
        render_coverage,
        render_diagnostics,
        render_install,
        render_tests,
    )
    from engineering_team.devtools.runner import DevRunner

    runner = DevRunner(env.ctx)

    def call(name: str, operation: Callable[[], str], **arguments: object) -> str:
        return env.run(
            name, lambda: bounded(operation(), hint="narrow the paths"), arguments=arguments
        )

    @tool("Run Tests")
    def run_tests(
        path: str = "",
        filter: str = "",
        markers: str = "",
        framework: str = "",
        fail_fast: bool = False,
        timeout_seconds: int = 0,
        working_directory: str = ".",
    ) -> str:
        """Run the project's tests and get a compact result: counts and each failing test with
        file, line, and message (not a raw log).

        Framework is detected (pytest, unittest, jest, vitest, go, cargo, maven, gradle, dotnet,
        rspec, minitest, phpunit). path may list several comma-separated; filter is a name
        expression; markers is pytest -m. Example: path='tests/test_api.py', filter='login'.
        """

        def operation() -> str:
            directory, _ = runner.resolve(working_directory)
            selection = runner.selection(
                directory, _split(path), filter=filter, markers=markers, fail_fast=fail_fast
            )
            report = runner.run_tests(
                working_directory,
                selection,
                framework=framework or None,
                timeout=timeout_seconds or None,
            )
            return render_tests(report)

        return call(
            "Run Tests",
            operation,
            path=path,
            filter=filter,
            markers=markers,
            framework=framework,
            fail_fast=fail_fast,
            working_directory=working_directory,
        )

    @tool("Rerun Failed Tests")
    def rerun_failed_tests(timeout_seconds: int = 0, working_directory: str = ".") -> str:
        """Run again exactly the tests that failed in the last Run Tests of this project
        directory. Use it after a fix; the result has the same compact form. It fails if there
        was no earlier run or nothing failed.
        """

        return call(
            "Rerun Failed Tests",
            lambda: render_tests(runner.rerun_failed(working_directory, timeout_seconds or None)),
            working_directory=working_directory,
        )

    @tool("Run Single Test")
    def run_single_test(
        test_id: str, timeout_seconds: int = 0, working_directory: str = "."
    ) -> str:
        """Run one test by the id a test report lists, to iterate on it quickly.
        Example: test_id='tests/test_api.py::test_login' (pytest), 'pkg.mod.Class.test_x'
        (unittest), 'src/a.test.js::adds numbers' (jest), 'Class#method' (JUnit, minitest).
        """

        return call(
            "Run Single Test",
            lambda: render_tests(
                runner.run_single(working_directory, test_id, timeout_seconds or None)
            ),
            test_id=test_id,
            working_directory=working_directory,
        )

    @tool("Run Linter")
    def run_linter(
        path: str = "", tool: str = "", timeout_seconds: int = 0, working_directory: str = "."
    ) -> str:
        """Lint the project and list problems as file:line:col, rule, and message, errors first
        (capped). The linter is detected (ruff, eslint, golangci-lint, clippy, rubocop, phpcs);
        pass tool= to choose. path limits it to files or folders, comma-separated.
        """

        return call(
            "Run Linter",
            lambda: render_diagnostics(
                runner.lint(working_directory, _split(path), tool, timeout_seconds or None)
            ),
            path=path,
            tool=tool,
            working_directory=working_directory,
        )

    @tool("Type Check")
    def type_check(
        path: str = "", tool: str = "", timeout_seconds: int = 0, working_directory: str = "."
    ) -> str:
        """Type-check the project (mypy, pyright, tsc, go vet, cargo check) and list errors as
        file:line:col, rule, and message. The checker is detected; pass tool= to choose. Run it
        before declaring work done in a typed project.
        """

        return call(
            "Type Check",
            lambda: render_diagnostics(
                runner.typecheck(working_directory, _split(path), tool, timeout_seconds or None)
            ),
            path=path,
            tool=tool,
            working_directory=working_directory,
        )

    @tool("Format Code")
    def format_code(
        path: str = "",
        mode: str = "check",
        tool: str = "",
        timeout_seconds: int = 0,
        working_directory: str = ".",
    ) -> str:
        """Check or apply code formatting (ruff format, black, prettier, gofmt, rustfmt).
        mode='check' lists files that need formatting and changes nothing; mode='write' reformats
        them in place (see Workspace Changes afterwards). path limits it, comma-separated.
        """

        def operation() -> str:
            if mode not in ("check", "write"):
                return "ERROR: mode must be 'check' or 'write'."
            report = runner.format_code(
                working_directory,
                _split(path),
                tool,
                write=mode == "write",
                timeout=timeout_seconds or None,
            )
            return render_diagnostics(report)

        return call(
            "Format Code",
            operation,
            path=path,
            mode=mode,
            tool=tool,
            working_directory=working_directory,
        )

    @tool("Build Project")
    def build_project(timeout_seconds: int = 0, working_directory: str = ".") -> str:
        """Build the project with its detected build command (npm run build, go build, cargo
        build, mvn compile, dotnet build, uv build ...) and list compile errors as
        file:line:col and message. Without a detected build it says so.
        """

        return call(
            "Build Project",
            lambda: render_diagnostics(runner.build(working_directory, timeout_seconds or None)),
            working_directory=working_directory,
        )

    @tool("Coverage Report")
    def coverage_report(
        file: str = "", path: str = "", timeout_seconds: int = 0, working_directory: str = "."
    ) -> str:
        """Run the tests under coverage (coverage.py, jest/vitest, go, cargo-llvm-cov) and give
        the total, the least-covered files, and the test result. Pass file= to list that
        file's uncovered line ranges, so you know which tests to add.
        """

        return call(
            "Coverage Report",
            lambda: render_coverage(
                runner.coverage(
                    working_directory, file, tuple(_split(path)), timeout_seconds or None
                )
            ),
            file=file,
            path=path,
            working_directory=working_directory,
        )

    @tool("Install Dependencies")
    def install_dependencies(timeout_seconds: int = 0, working_directory: str = ".") -> str:
        """Install the project's declared dependencies with its package manager (uv, pip, npm,
        pnpm, yarn, bun, go, cargo, maven, bundler, composer ...), preferring lockfiles. It
        needs the network (the setup phase). Use it before running tests in a fresh project.
        """

        return call(
            "Install Dependencies",
            lambda: render_install(runner.install(working_directory, timeout_seconds or None)),
            working_directory=working_directory,
        )

    @tool("Dependency Audit")
    def dependency_audit(timeout_seconds: int = 0, working_directory: str = ".") -> str:
        """Check dependencies for known vulnerabilities with pip-audit, npm/pnpm audit, or
        cargo audit. Lists package, version, severity, and the fixed version. If the audit tool
        is missing the result is UNAVAILABLE, never an empty pass. Needs the network.
        """

        return call(
            "Dependency Audit",
            lambda: render_audit(runner.audit(working_directory, timeout_seconds or None)),
            working_directory=working_directory,
        )

    return {
        "Run Tests": run_tests,
        "Rerun Failed Tests": rerun_failed_tests,
        "Run Single Test": run_single_test,
        "Run Linter": run_linter,
        "Type Check": type_check,
        "Format Code": format_code,
        "Build Project": build_project,
        "Coverage Report": coverage_report,
        "Install Dependencies": install_dependencies,
        "Dependency Audit": dependency_audit,
    }
