"""Command lines for each framework and tool, and how to read what they produce.

Pure construction: nothing here runs a process. A plan names the argv, the report files the
command should write (inside the workspace scratch directory), and the parser for its output.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.devtools.detect import Stack
from engineering_team.devtools.models import (
    TestFailure,
    TestReport,
)
from engineering_team.devtools.parsers.junit import parse_junit
from engineering_team.devtools.parsers.tests_json import parse_go_test, parse_jest, parse_rspec
from engineering_team.devtools.parsers.tests_text import (
    parse_cargo_test,
    parse_minitest,
    parse_unittest,
)
from engineering_team.devtools.parsers.trx import parse_trx
from engineering_team.tools.support import ToolError

SUPPORTED_TESTS = (
    "pytest, unittest, jest, vitest, go, cargo, maven, gradle, dotnet, rspec, minitest, phpunit"
)
SUPPORTED_LINTERS = ("ruff", "eslint", "golangci-lint", "clippy", "rubocop", "phpcs")
SUPPORTED_TYPECHECKERS = ("mypy", "pyright", "tsc", "go-vet", "cargo-check")
SUPPORTED_FORMATTERS = ("ruff", "black", "prettier", "gofmt", "rustfmt")


@dataclass(frozen=True)
class Selection:
    """What to run: paths (relative to the project directory), a name filter, and exact ids."""

    paths: tuple[str, ...] = ()
    filter: str = ""
    markers: str = ""
    fail_fast: bool = False
    failures: tuple[TestFailure, ...] = ()  # exact tests to run (rerun, single test)


@dataclass
class Paths:
    """Where a command may write its reports; the runner picks the files."""

    report: Path  # a file for single-file reports
    directory: Path  # a scratch directory for tools that write several files


@dataclass
class TestCommand:
    __test__ = False

    argv: list[str]
    framework: str
    # (log text, report text or None) -> TestReport. ``reports`` lists the files to read.
    parse: Callable[[str, str | None], TestReport]
    reports: list[Path] = field(default_factory=list)


def python_argv(directory: Path, tool: str, *, module: bool = False) -> list[str]:
    """How to start a Python tool in ``directory``: its own virtual environment first, then a
    tool on ``PATH`` (unless the tool must run as ``python -m`` so the project is importable)."""

    venv = directory / ".venv" / "bin" / "python"
    if venv.exists():
        return [str(venv), "-m", tool.replace("-", "_")]
    if not module and shutil.which(tool):
        return [tool]
    return ["python" if shutil.which("python") else "python3", "-m", tool.replace("-", "_")]


def local_executable(directory: Path, relative: str, fallback: str) -> str:
    """``./vendor/bin/phpunit`` style project-local binaries, else the one on ``PATH``."""

    return f"./{relative}" if (directory / relative).is_file() else fallback


def go_module(directory: Path) -> str | None:
    try:
        text = (directory / "go.mod").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _split_id(test_id: str) -> tuple[str, str]:
    """``file::name`` -> (file, name); ids without a separator are all name."""

    head, sep, tail = test_id.partition("::")
    return (head, tail) if sep else ("", test_id)


def _unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


# -- tests -------------------------------------------------------------------------------


def build_test_command(
    stack: Stack,
    directory: Path,
    selection: Selection,
    paths: Paths,
    *,
    framework: str | None = None,
) -> TestCommand:
    """The command (and report parser) that runs ``stack``'s tests as ``selection`` asks."""

    name = framework or stack.test
    builder = TEST_BUILDERS.get(name or "")
    if builder is None:
        raise ToolError(
            f"No test framework detected in {stack.directory!r}. Pass framework= one of: "
            f"{SUPPORTED_TESTS}; or use Run Script / Run Project Command for the project's own."
        )
    return builder(stack, directory, selection, paths)


def _pytest(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = [*python_argv(directory, "pytest", module=True), "-q", "--tb=short"]
    argv.append(f"--junitxml={paths.report}")
    if sel.fail_fast:
        argv.append("-x")
    if sel.filter:
        argv += ["-k", sel.filter]
    if sel.markers:
        argv += ["-m", sel.markers]
    targets = _unique([f.test_id for f in sel.failures] or list(sel.paths))
    argv += targets
    return TestCommand(
        argv,
        "pytest",
        lambda log, xml: _from_report(
            xml, log, lambda x: parse_junit(x, framework="pytest", flavor="pytest")
        ),
        [paths.report],
    )


def _unittest(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = [*python_argv(directory, "unittest", module=True)]
    if sel.fail_fast:
        argv.append("-f")
    if sel.filter:
        argv += ["-k", sel.filter]
    ids = _unique([f.test_id for f in sel.failures])
    if ids:
        argv += ids
    elif len(sel.paths) == 1 and (directory / sel.paths[0]).is_file():
        argv.append(sel.paths[0].removesuffix(".py").replace("/", "."))
    else:
        argv += ["discover", "-t", ".", *(["-s", sel.paths[0]] if sel.paths else [])]
    return TestCommand(argv, "unittest", lambda log, _: parse_unittest(log, root=str(directory)))


def _jest_like(framework: str) -> Callable[[Stack, Path, Selection, Paths], TestCommand]:
    def build(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
        argv = ["npx", "--no-install"]
        if framework == "vitest":
            argv += ["vitest", "run", "--reporter=json", f"--outputFile={paths.report}"]
            argv += ["--bail=1"] if sel.fail_fast else []
        else:
            argv += ["jest", "--ci", "--json", f"--outputFile={paths.report}"]
            argv += ["--bail"] if sel.fail_fast else []
        names = sel.filter
        files = list(sel.paths)
        if sel.failures:
            split = [_split_id(f.test_id) for f in sel.failures]
            files = _unique([f for f, _ in split])
            names = "^(" + "|".join(re.escape(n) for _, n in split if n) + ")$"
        if names:
            argv += ["-t", names]
        argv += files
        return TestCommand(
            argv,
            framework,
            lambda log, text: _from_report(
                text, log, lambda x: parse_jest(x, root=str(directory), framework=framework)
            ),
            [paths.report],
        )

    return build


def _go(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = ["go", "test", "-json", "-count=1"]
    if sel.fail_fast:
        argv.append("-failfast")
    packages: list[str] = []
    if sel.failures:
        names, packages = [], []
        for failure in sel.failures:
            package, name = _split_id(failure.test_id)
            names.append(re.escape(name.split("/")[0]))
            packages.append(package)
        argv += ["-run", "^(" + "|".join(_unique(names)) + ")$"]
    elif sel.filter:
        argv += ["-run", sel.filter]
    argv += (
        _unique(packages)
        or [f"./{p.strip('./')}/..." if p not in ("", ".") else "./..." for p in sel.paths]
        or ["./..."]
    )
    module = go_module(directory)
    return TestCommand(argv, "go", lambda log, _: parse_go_test(log, module=module))


def _cargo(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = ["cargo", "test"]
    if not sel.fail_fast:
        argv.append("--no-fail-fast")
    filters = _unique([f.test_id for f in sel.failures]) or ([sel.filter] if sel.filter else [])
    if filters:
        argv += ["--", *(["--exact"] if sel.failures else []), *filters]
    return TestCommand(argv, "cargo", lambda log, _: parse_cargo_test(log))


def _maven(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = [local_executable(directory, "mvnw", "mvn"), "-B", "test", "-DfailIfNoTests=false"]
    if sel.fail_fast:
        argv.append("-Dsurefire.skipAfterFailureCount=1")
    selector = ",".join(_unique([f.test_id for f in sel.failures])) or sel.filter
    if selector:
        argv.append(f"-Dtest={selector}")
    reports = directory / "target" / "surefire-reports"
    return TestCommand(
        argv, "maven", lambda log, _: _junit_directory(reports, "maven", log), [reports]
    )


def _gradle(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = [local_executable(directory, "gradlew", "gradle"), "test"]
    argv.append("--fail-fast" if sel.fail_fast else "--continue")
    for failure in sel.failures:
        argv += ["--tests", failure.test_id.replace("#", ".")]
    if sel.filter and not sel.failures:
        argv += ["--tests", sel.filter]
    reports = directory / "build" / "test-results" / "test"
    return TestCommand(
        argv, "gradle", lambda log, _: _junit_directory(reports, "gradle", log), [reports]
    )


def _dotnet(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = ["dotnet", "test", "--nologo", "--logger", f"trx;LogFileName={paths.report}"]
    ids = _unique([f.test_id for f in sel.failures])
    if ids:
        argv += ["--filter", "|".join(f"FullyQualifiedName={i}" for i in ids)]
    elif sel.filter:
        argv += ["--filter", f"FullyQualifiedName~{sel.filter}"]
    argv += list(sel.paths)
    return TestCommand(
        argv,
        "dotnet",
        lambda log, xml: _from_report(xml, log, lambda x: parse_trx(x, root=str(directory))),
        [paths.report],
    )


def _rspec(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    prefix = ["bundle", "exec", "rspec"] if (directory / "Gemfile").exists() else ["rspec"]
    argv = [*prefix, "--format", "json", "--out", str(paths.report)]
    if sel.fail_fast:
        argv.append("--fail-fast")
    if sel.filter:
        argv += ["-e", sel.filter]
    argv += _unique([f.test_id for f in sel.failures]) or list(sel.paths)
    return TestCommand(
        argv, "rspec", lambda log, text: _from_report(text, log, parse_rspec), [paths.report]
    )


def _minitest(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    files = _unique([f.file or "" for f in sel.failures]) or list(sel.paths)
    names = _unique([f.test_id.split("#", 1)[-1] for f in sel.failures])
    options = (
        ["-n", "/^(" + "|".join(map(re.escape, names)) + ")$/"]
        if names
        else (["-n", f"/{sel.filter}/"] if sel.filter else [])
    )
    if files:
        argv = ["ruby", "-Itest", "-Ilib", *files, *options]
    else:
        prefix = ["bundle", "exec"] if (directory / "Gemfile").exists() else []
        argv = [*prefix, "rake", "test"]
    return TestCommand(argv, "minitest", lambda log, _: parse_minitest(log))


def _phpunit(stack: Stack, directory: Path, sel: Selection, paths: Paths) -> TestCommand:
    argv = [
        local_executable(directory, "vendor/bin/phpunit", "phpunit"),
        "--log-junit",
        str(paths.report),
    ]
    if sel.fail_fast:
        argv.append("--stop-on-failure")
    names = _unique([f.test_id.split("#", 1)[-1] for f in sel.failures])
    if names:
        argv += ["--filter", "::(" + "|".join(map(re.escape, names)) + ")$"]
    elif sel.filter:
        argv += ["--filter", sel.filter]
    argv += _unique([f.file or "" for f in sel.failures]) or list(sel.paths)
    return TestCommand(
        argv,
        "phpunit",
        lambda log, xml: _from_report(xml, log, lambda x: parse_junit(x, framework="phpunit")),
        [paths.report],
    )


TEST_BUILDERS: dict[str, Callable[[Stack, Path, Selection, Paths], TestCommand]] = {
    "pytest": _pytest,
    "unittest": _unittest,
    "jest": _jest_like("jest"),
    "vitest": _jest_like("vitest"),
    "go": _go,
    "cargo": _cargo,
    "maven": _maven,
    "gradle": _gradle,
    "dotnet": _dotnet,
    "rspec": _rspec,
    "minitest": _minitest,
    "phpunit": _phpunit,
}


def _from_report(text: str | None, log: str, parse: Callable[[str], TestReport]) -> TestReport:
    """Parse the framework's report file; without one the run crashed before writing it."""

    if not text:
        return TestReport(status="error", raw_tail=log)
    return parse(text)


def _junit_directory(directory: Path, framework: str, log: str) -> TestReport:
    """Merge every ``TEST-*.xml`` a Maven or Gradle run wrote."""

    files = sorted(directory.glob("*.xml")) if directory.is_dir() else []
    if not files:
        return TestReport(framework=framework, status="error", raw_tail=log)
    reports = [parse_junit(f.read_text(encoding="utf-8"), framework=framework) for f in files]
    return TestReport(
        framework=framework,
        status="failed" if any(r.status == "failed" for r in reports) else "passed",
        passed=sum(r.passed for r in reports),
        failed=sum(r.failed for r in reports),
        skipped=sum(r.skipped for r in reports),
        errors=sum(r.errors for r in reports),
        duration=round(sum(r.duration for r in reports), 3),
        failures=[f for r in reports for f in r.failures],
    )
