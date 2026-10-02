"""The developer tools end to end: a real tiny Python project, a scripted backend, an agent."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import Toolbox

from engineering_team.devtools.runner import DEV_EXECUTABLES, DevRunner
from engineering_team.execution.backend import CommandRecord, CommandSpec
from engineering_team.runtime.context import RunContext
from engineering_team.settings import load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import ProjectWorkspace, build_tools
from engineering_team.tools.commands import prepare_command
from engineering_team.tools.support import ToolError
from engineering_team.tools.workspace import WorkspaceError

MakeToolbox = Callable[..., Toolbox]

PYPROJECT = '[project]\nname = "calc"\nversion = "0.1.0"\n[tool.pytest.ini_options]\n'
CALC = "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    return a / b\n"
TESTS = """import pytest

from calc import add, div


def test_add():
    assert add(1, 2) == 3


def test_add_wrong():
    assert add(1, 2) == 4, "one plus two should be three"


def test_div_by_zero():
    assert div(1, 0) == 0
"""


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commands the tools run must find the interpreter (and pytest, ruff, mypy) running us."""

    scripts = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", scripts + ":" + __import__("os").environ["PATH"])


@pytest.fixture
def box(make_toolbox: MakeToolbox) -> Toolbox:
    box = make_toolbox(groups=["dev"])
    box.write("pyproject.toml", PYPROJECT)
    box.write("calc.py", CALC)
    box.write("tests/test_calc.py", TESTS)
    return box


# -- Run Tests ---------------------------------------------------------------------------


def test_a_failing_run_is_a_compact_list_of_failures_not_a_log(box: Toolbox) -> None:
    result = box("Run Tests")

    lines = result.splitlines()
    assert (
        lines[0]
        == "Tests (pytest): FAILED - 1 passed, 2 failed, 0 skipped in "
        + (lines[0].rsplit("in ", 1)[1])
    )
    assert "Failures (2):" in result
    assert "1. tests/test_calc.py::test_add_wrong  [tests/test_calc.py:11]" in result
    assert "AssertionError: one plus two should be three" in result
    assert "2. tests/test_calc.py::test_div_by_zero  [tests/test_calc.py:15]" in result
    assert "ZeroDivisionError: division by zero" in result
    assert "full log: .engineering-team/runs/" in result and "(exit 1)" in result
    assert len(result) < 3000


def test_a_huge_log_still_gives_a_small_result(box: Toolbox) -> None:
    noisy = TESTS.replace(
        'def test_add_wrong():\n    assert add(1, 2) == 4, "one plus two should be three"',
        "def test_add_wrong():\n    for n in range(4000):\n"
        "        print('noise line', n, 'x' * 40)\n    assert add(1, 2) == 4",
    )
    box.write("tests/test_calc.py", noisy)

    result = box("Run Tests")

    assert len(result) < 3500 and "noise line" not in result
    log = box.workspace.root / result.split("full log: ")[1].splitlines()[0].split(" ")[0]
    assert "noise line 3999" in log.read_text(encoding="utf-8") or log.stat().st_size > 100_000


def test_a_passing_run_says_so_and_a_selection_narrows_it(box: Toolbox) -> None:
    result = box("Run Tests", path="tests/test_calc.py", filter="test_add and not wrong")

    assert result.splitlines()[0].startswith("Tests (pytest): PASSED - 1 passed, 0 failed")
    assert "Failures" not in result


def test_a_run_that_collects_no_tests_is_an_error_not_a_pass(box: Toolbox) -> None:
    result = box("Run Tests", filter="matches_nothing_at_all")

    assert result.splitlines()[0] == "Tests (pytest): ERROR"
    assert "No tests ran" in result


def test_a_collection_error_is_reported_with_its_cause(box: Toolbox) -> None:
    box.write("tests/test_broken.py", "import missing_module_xyz\n")

    result = box("Run Tests", path="tests/test_broken.py")

    assert "0 passed, 0 failed, 1 errors" in result and "tests/test_broken.py" in result
    assert "No module named 'missing_module_xyz'" in result


def test_rerun_failed_and_run_single_test(box: Toolbox) -> None:
    assert box("Rerun Failed Tests").startswith("ERROR: No earlier test run")
    box("Run Tests")

    again = box("Rerun Failed Tests")
    assert (
        again.splitlines()[0].split(" in ")[0]
        == "Tests (pytest): FAILED - 0 passed, 2 failed, 0 skipped"
    )
    assert "test_add_wrong" in again and "test_add " not in again

    single = box("Run Single Test", test_id="tests/test_calc.py::test_add")
    assert single.splitlines()[0].startswith("Tests (pytest): PASSED - 1 passed, 0 failed")
    assert box("Run Single Test", test_id="--help").startswith("ERROR:")


def test_after_the_fix_the_rerun_passes_and_then_there_is_nothing_to_rerun(box: Toolbox) -> None:
    box("Run Tests")
    box.write("tests/test_calc.py", TESTS.replace("== 4,", "== 3,").replace("== 0", "== 0.0"))
    box.write("calc.py", CALC.replace("a / b", "0.0 if b == 0 else a / b"))

    assert box("Rerun Failed Tests").splitlines()[0].startswith("Tests (pytest): PASSED")
    assert box("Rerun Failed Tests").startswith("ERROR: Nothing to rerun")


def test_unittest_projects_are_detected_and_parsed(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["dev"])
    box.write("calc.py", CALC)
    box.write(
        "tests/test_ut.py",
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertEqual(add(1, 2), 3)\n\n"
        "    def test_bad(self):\n        self.assertEqual(add(1, 2), 4)\n",
    )
    box.write("tests/__init__.py", "")

    result = box("Run Tests")

    assert result.startswith("Tests (unittest): FAILED - 1 passed, 1 failed")
    assert "tests.test_ut.T.test_bad" in result and "tests/test_ut.py" in result


def test_a_project_without_a_known_framework_returns_its_script_output(
    make_toolbox: MakeToolbox,
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    box = make_toolbox(groups=["dev"])
    box.write("pyproject.toml", '[project]\nname = "x"\n')
    box.write("Makefile", "test:\n\t@echo everything is fine\n")

    result = box("Run Tests")

    assert result.startswith("Tests (unknown): PASSED")
    assert "everything is fine" in result and "Unrecognised test framework" in result


def test_no_project_lists_where_projects_are(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["dev"])
    box.write("backend/pyproject.toml", PYPROJECT)

    assert "Projects found: backend (python (pip)" in box("Run Tests")
    assert box("Run Tests", working_directory="backend").startswith("Tests (pytest)")
    empty = make_toolbox("empty", groups=["dev"])
    assert "No project detected" in empty("Run Tests")


@pytest.mark.parametrize(
    "arguments",
    [{"path": "--help"}, {"filter": "-x"}, {"markers": "--collect-only"}, {"path": "../outside"}],
)
def test_arguments_cannot_become_flags_or_leave_the_project(
    box: Toolbox, arguments: dict[str, str]
) -> None:
    assert box("Run Tests", **arguments).startswith("ERROR:")


def test_results_are_the_same_shape_in_a_subdirectory_project(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["dev"])
    box.write("api/pyproject.toml", PYPROJECT)
    box.write("api/calc.py", CALC)
    box.write("api/tests/test_calc.py", TESTS)

    result = box("Run Tests", working_directory="api")

    assert "[api/tests/test_calc.py:11]" in result  # relative to the project root, not to api/


# -- lint, types, format ----------------------------------------------------------------


def test_the_linter_lists_problems_with_location_and_rule(box: Toolbox) -> None:
    box.write("pyproject.toml", PYPROJECT + "[tool.ruff]\n")
    box.write("messy.py", "import os, sys\n\n\ndef f(x):\n    return x\n")

    result = box("Run Linter", path="messy.py")

    assert result.splitlines()[0] == "Lint (ruff): FAILED - 3 errors, 0 warnings"
    assert "messy.py:1:1 error I001:" in result
    assert "messy.py:1:8 error F401: `os` imported but unused" in result


def test_a_clean_project_lints_clean(box: Toolbox) -> None:
    box.write("pyproject.toml", PYPROJECT + "[tool.ruff]\n")

    assert box("Run Linter", path="calc.py").splitlines()[0] == "Lint (ruff): PASSED"


def test_lint_without_a_detected_linter_names_the_choices(box: Toolbox) -> None:
    assert "Pass tool= one of: ruff, eslint" in box("Run Linter")


def test_type_check_lists_mypy_errors(box: Toolbox) -> None:
    box.write("pyproject.toml", PYPROJECT + "[tool.mypy]\n")
    box.write("typed.py", "def f(x: int) -> str:\n    return x\n")

    result = box("Type Check", path="typed.py")

    assert result.splitlines()[0] == "Type check (mypy): FAILED - 1 errors, 0 warnings"
    assert "typed.py:2:12 error return-value: Incompatible return value type" in result


def test_format_check_then_write(box: Toolbox) -> None:
    box.write("pyproject.toml", PYPROJECT + "[tool.ruff]\n")
    box.write("ugly.py", "x = {  'a':1 }\n")

    checked = box("Format Code", path="ugly.py")
    assert checked.splitlines()[0] == "Format (ruff): FAILED - 1 file(s) need formatting"
    assert "ugly.py" in checked and "mode='write'" in checked
    assert box.read("ugly.py") == "x = {  'a':1 }\n"  # check mode changes nothing

    written = box("Format Code", path="ugly.py", mode="write")
    assert written.splitlines()[0] == "Format (ruff): PASSED"
    assert "Workspace Changes" in written and box.read("ugly.py") == 'x = {"a": 1}\n'
    assert box("Format Code", mode="sideways").startswith("ERROR: mode must be")


def test_build_needs_a_detected_build_command(box: Toolbox) -> None:
    assert box("Build Project").startswith("ERROR: No build command detected")


# -- unavailable tools ------------------------------------------------------------------


@pytest.fixture
def hide(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    real = shutil.which

    def hider(*names: str) -> None:
        monkeypatch.setattr(
            shutil, "which", lambda cmd, *a, **k: None if cmd in names else real(cmd, *a, **k)
        )

    return hider


def test_a_missing_executable_is_unavailable_with_an_install_hint(
    box: Toolbox, hide: Callable[..., None]
) -> None:
    box.write("package.json", "{}")  # python wins detection; ask for the JS checker explicitly
    hide("npx")

    result = box("Type Check", tool="tsc")

    assert result.splitlines()[0] == "Type check (tsc): UNAVAILABLE"
    assert "Hint: Install Node.js" in result and "PASSED" not in result


def test_a_missing_python_module_is_unavailable_too(
    box: Toolbox, hide: Callable[..., None]
) -> None:
    hide("pyright")

    result = box("Type Check", tool="pyright")

    assert "UNAVAILABLE" in result and "pyright" in result


# -- the scripted backend: coverage, install, audit, limits ----------------------------


class ScriptedBackend:
    """Answers commands from a table; a handler may write the report files a tool expects."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.specs: list[CommandSpec] = []
        self.timed_out = False
        self.handlers: list[tuple[str, Callable[[CommandSpec], tuple[int, str]]]] = []

    def on(self, needle: str, handler: Callable[[CommandSpec], tuple[int, str]]) -> None:
        self.handlers.append((needle, handler))

    def run(self, spec: CommandSpec) -> CommandRecord:
        self.specs.append(spec)
        exit_code, output = 0, ""
        command = " ".join(spec.argv)
        for needle, handler in self.handlers:
            if needle in command:
                exit_code, output = handler(spec)
                break
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_dir / f"{len(self.specs)}.log"
        log.write_text(output, encoding="utf-8")
        return CommandRecord(
            id=f"cmd-{len(self.specs)}",
            argv=spec.argv,
            cwd=spec.cwd,
            exit_code=exit_code,
            timed_out=self.timed_out,
            duration=0.5,
            log_path=log,
            output_tail=output,
            truncated=False,
        )

    def start(self, spec: CommandSpec) -> Any:
        raise NotImplementedError


def scripted(tmp_path: Path, **overrides: Any) -> tuple[RunContext, ScriptedBackend, Toolbox]:
    workspace = ProjectWorkspace.create(tmp_path / "scripted")
    settings = load_settings(overrides=overrides or None)
    backend = ScriptedBackend(tmp_path / "scripted-logs")
    ctx = RunContext.create(settings, workspace, backend=backend)
    return ctx, backend, Toolbox(ctx, build_tools(ctx, groups=["dev"]))


def arg_after(spec: CommandSpec, prefix: str) -> str:
    return next(a for a in spec.argv if a.startswith(prefix)).removeprefix(prefix)


def test_coverage_combines_the_test_result_with_the_uncovered_lines(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT)
    box.write("calc.py", CALC)
    box.write("tests/test_calc.py", TESTS)

    def tests(spec: CommandSpec) -> tuple[int, str]:
        Path(arg_after(spec, "--junitxml=")).write_text(
            '<testsuite><testcase classname="tests.test_calc" name="test_add"/></testsuite>',
            encoding="utf-8",
        )
        return 0, ""

    def report(spec: CommandSpec) -> tuple[int, str]:
        Path(spec.argv[spec.argv.index("-o") + 1]).write_text(
            json.dumps(
                {
                    "files": {
                        "calc.py": {
                            "summary": {"covered_lines": 2, "num_statements": 4},
                            "missing_lines": [5, 6],
                        },
                        "util.py": {
                            "summary": {"covered_lines": 3, "num_statements": 3},
                            "missing_lines": [],
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return 0, ""

    backend.on(" run ", tests)
    backend.on(" json ", report)

    summary = box("Coverage Report")
    assert (
        summary.splitlines()[0]
        == "Coverage (coverage.py): PASSED - 71.4% (5/7 lines or statements)"
    )
    assert "Tests: passed (1 passed" in summary and "- calc.py: 50% (2/4)" in summary

    detail = box("Coverage Report", file="calc.py")
    assert "calc.py: 50% (2/4)\nUncovered lines: 5-6" in detail
    assert "No coverage data for 'nope.py'" in box("Coverage Report", file="nope.py")


def test_coverage_with_the_tool_not_installed_is_unavailable(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)
    backend.on("coverage", lambda spec: (1, "/usr/bin/python: No module named coverage\n"))

    result = box("Coverage Report")

    assert result.splitlines()[0] == "Coverage (coverage.py): UNAVAILABLE"
    assert "python -m pip install coverage" in result or "uv add --dev coverage" in result


def test_install_asks_for_the_network_and_uses_the_lockfile(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("package.json", "{}")
    box.write("package-lock.json", "{}")
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed")
    backend.on("npm ci", lambda spec: (0, "added 12 packages\n"))

    result = box("Install Dependencies")

    assert result.splitlines()[0] == "Install (npm): PASSED"
    (spec,) = backend.specs
    assert spec.argv == ("npm", "ci") and spec.network is True
    # Nothing else asks for the network.
    box.write("calc.py", CALC)


def test_a_failed_install_shows_the_output_and_a_network_hint(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("requirements.txt", "flask\n")
    backend.on(
        "pip install", lambda spec: (1, "ERROR: Max retries exceeded with url: /simple/flask\n")
    )

    result = box("Install Dependencies")

    assert result.splitlines()[0] == "Install (pip): FAILED"
    assert "Max retries exceeded" in result and "Needs network access" in result


def test_audit_lists_vulnerabilities_and_missing_tools_are_not_passes(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("requirements.txt", "requests==2.19.0\n")
    fixture = Path(__file__).parent / "fixtures" / "devtools" / "pip_audit.json"
    backend.on("pip_audit", lambda spec: (1, fixture.read_text(encoding="utf-8")))
    backend.on("pip-audit", lambda spec: (1, fixture.read_text(encoding="utf-8")))

    result = box("Dependency Audit")
    assert (
        result.splitlines()[0] == "Dependency audit (pip-audit): FAILED - 1 vulnerable package(s)"
    )
    assert "requests 2.19.0 [unknown] PYSEC-2018-28" in result and "fixed in 2.20.0" in result

    backend.handlers.clear()
    backend.on("pip", lambda spec: (1, "/usr/bin/python: No module named pip_audit\n"))
    missing = box("Dependency Audit")
    assert missing.splitlines()[0] == "Dependency audit (pip-audit): UNAVAILABLE"
    assert "PASSED" not in missing


def test_network_tools_can_be_refused_by_setting(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path, **{"tools.dev.allow_network": False})
    box.write("requirements.txt", "flask\n")

    for name in ("Install Dependencies", "Dependency Audit"):
        assert "tools.dev.allow_network is false" in box(name)
    assert backend.specs == []


def test_timeouts_come_from_settings_and_a_call_may_only_shorten_them(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path, **{"tools.dev.test_timeout": 50})
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)

    box("Run Tests")
    box("Run Tests", timeout_seconds=10)
    box("Run Tests", timeout_seconds=9999)

    assert [spec.timeout for spec in backend.specs] == [50.0, 10.0, 50.0]


def test_the_failure_cap_comes_from_settings(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path, **{"tools.dev.max_failures": 2})
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)
    cases = "".join(
        f'<testcase classname="tests.test_calc" name="test_{n}"><failure message="bad {n}">'
        f"tests/test_calc.py:{n}: AssertionError</failure></testcase>"
        for n in range(5)
    )

    def handler(spec: CommandSpec) -> tuple[int, str]:
        Path(arg_after(spec, "--junitxml=")).write_text(
            f"<testsuite>{cases}</testsuite>", encoding="utf-8"
        )
        return 1, ""

    backend.on("pytest", handler)

    result = box("Run Tests")

    assert "Failures (5):" in result and "... 3 more failing tests not shown." in result
    assert "3. tests/test_calc.py::test_2" not in result


def test_a_crashed_run_without_a_report_shows_the_output_tail(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)
    backend.on(
        "pytest", lambda spec: (4, "ERROR: usage: pytest [options]\npytest: error: bad flag\n")
    )

    result = box("Run Tests")

    assert result.splitlines()[0] == "Tests (pytest): ERROR"
    assert "pytest: error: bad flag" in result


# -- the allowlist -----------------------------------------------------------------------


def test_dev_executables_are_allowed_for_the_dev_tools_only(
    make_toolbox: MakeToolbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_toolbox(groups=["dev"])
    runner = DevRunner(box.ctx)
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/bin/{cmd}")

    assert "eslint" in DEV_EXECUTABLES
    prepare_command(runner.workspace, "eslint -f json .")  # the dev tools may run it
    with pytest.raises(WorkspaceError, match="not allowed"):
        prepare_command(box.ctx.workspace, "eslint -f json .")  # Run Project Command may not


def test_extra_executables_extend_the_dev_allowlist(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["dev"])
    runner = DevRunner(box.ctx)
    assert "bazel" not in runner.workspace.extra_commands

    settings = load_settings(overrides={"tools.dev.extra_executables": ["Bazel"]})
    ctx = RunContext.create(settings, box.ctx.workspace)

    assert "bazel" in DevRunner(ctx).workspace.extra_commands


def test_the_runner_reports_unknown_projects_as_tool_errors(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["dev"])

    with pytest.raises(ToolError, match="No project detected"):
        DevRunner(box.ctx).resolve(".")


# -- an agent uses the tools -------------------------------------------------------------


def test_an_agent_fails_reads_the_structured_failure_fixes_and_passes(
    make_context: Callable[..., RunContext],
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("pyproject.toml", PYPROJECT)
    ctx.workspace.write_file("calc.py", "def add(a, b):\n    return a - b\n")
    ctx.workspace.write_file(
        "tests/test_calc.py",
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    llm = ScriptedLLM(
        [
            ToolCall("Run Tests", {}),
            ToolCall(
                "Replace In Project File",
                {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"},
            ),
            ToolCall("Rerun Failed Tests", {}),
            "Fixed add(): it subtracted. The test passes now.",
        ]
    )
    tools = build_tools(ctx, groups=["fs_read", "fs_write", "dev"])

    result = run_agent_task(ctx, llm, tools=tools, max_iter=10)

    assert result.startswith("Fixed add()")
    llm.assert_exhausted()
    failure_prompt = llm.calls[1].prompt
    assert "tests/test_calc.py::test_add  [tests/test_calc.py:5]" in failure_prompt
    assert "assert -1 == 3" in failure_prompt  # the message the agent fixed it from
    assert "Tests (pytest): PASSED - 1 passed" in llm.calls[3].prompt
    assert (ctx.workspace.root / "calc.py").read_text(encoding="utf-8") == (
        "def add(a, b):\n    return a + b\n"
    )


# -- telling a missing tool from a project's own failure --------------------------------


@pytest.mark.parametrize(
    ("argv", "text", "expected"),
    [
        (
            ["python", "-m", "coverage", "run"],
            "/usr/bin/python: No module named coverage",
            "coverage",
        ),
        (["python", "-m", "pip_audit"], "No module named pip_audit", "pip-audit"),
        # The project's own broken import is not a missing tool.
        (["python", "-m", "pytest"], "ModuleNotFoundError: No module named 'flask'", None),
        (["make", "test"], "sh: pytest: command not found", None),
        (["cargo", "audit", "--json"], "error: no such command: `audit`", "audit"),
        (["bundle", "exec", "rspec"], 'Could not find command "rspec".', "rspec"),
        (
            ["npx", "--no-install", "jest"],
            "npm error could not determine executable to run",
            "jest",
        ),
        (["python", "-m", "pytest"], "No module named pytest", "pytest"),
    ],
)
def test_only_the_tool_we_ran_can_be_missing(
    argv: list[str], text: str, expected: str | None
) -> None:
    from engineering_team.devtools.availability import missing_tool

    assert missing_tool(argv, text, 1) == expected
    assert missing_tool(argv, text, 0) is None


def test_a_project_import_error_is_a_failure_not_an_unavailable_tool(box: Toolbox) -> None:
    box.write("tests/test_broken.py", "import missing_module_xyz\n")

    result = box("Run Tests", path="tests/test_broken.py")

    assert result.splitlines()[0].startswith("Tests (pytest): FAILED")
    assert "UNAVAILABLE" not in result and "No module named 'missing_module_xyz'" in result


# -- other stacks through the scripted backend ------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "devtools"


@pytest.fixture
def everything_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend every executable exists: the scripted backend never starts a real process."""

    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/bin/{cmd}")


@pytest.mark.usefixtures("everything_installed")
def test_a_rust_build_lists_compiler_errors_from_cargo_json(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("Cargo.toml", "[package]\nname = 'x'\n")
    cargo_json = (FIXTURES / "cargo_check.jsonl").read_text(encoding="utf-8")
    backend.on("cargo build", lambda spec: (101, cargo_json))

    result = box("Build Project")

    assert result.splitlines()[0] == "Build (cargo build): FAILED - 1 errors, 1 warnings"
    assert "src/lib.rs:4:5 error E0308: mismatched types" in result
    assert backend.specs[0].argv == ("cargo", "build", "--message-format=json")


@pytest.mark.usefixtures("everything_installed")
def test_go_vet_and_go_tests_through_the_same_tools(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("go.mod", "module example.com\n\ngo 1.21\n")
    backend.on("go vet", lambda spec: (1, (FIXTURES / "govet.txt").read_text(encoding="utf-8")))
    backend.on("go test", lambda spec: (1, (FIXTURES / "gotest.jsonl").read_text(encoding="utf-8")))

    vet = box("Type Check")
    assert vet.splitlines()[0] == "Type check (go-vet): FAILED - 3 errors, 0 warnings"
    assert "bad.go:4:9 error: undefined: undefinedName" in vet

    tests = box("Run Tests", filter="TestAdd")
    assert tests.splitlines()[0].startswith(
        "Tests (go): FAILED - 1 passed, 2 failed, 1 errors, 1 skipped"
    )
    assert "example.com/calc::TestAddWrong  [calc/calc_test.go:13]" in tests
    assert "Add(1, 2) = 3, want 4" in tests
    assert "-run" in backend.specs[-1].argv and "TestAdd" in backend.specs[-1].argv


@pytest.mark.usefixtures("everything_installed")
def test_a_report_that_cannot_be_read_is_an_error_with_the_output(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)

    def garbage(spec: CommandSpec) -> tuple[int, str]:
        Path(arg_after(spec, "--junitxml=")).write_text("not xml at all", encoding="utf-8")
        return 1, "something went wrong in the plugin\n"

    backend.on("pytest", garbage)

    result = box("Run Tests")

    assert result.splitlines()[0] == "Tests (pytest): ERROR"
    assert "report could not be read" in result and "something went wrong" in result


@pytest.mark.usefixtures("everything_installed")
def test_a_timeout_is_an_error_that_names_the_setting(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT)
    box.write("tests/test_calc.py", TESTS)
    backend.timed_out = True

    result = box("Run Tests")

    assert result.splitlines()[0] == "Tests (pytest): ERROR"
    assert "Timed out after" in result and "tools.dev.*_timeout" in result


@pytest.mark.usefixtures("everything_installed")
def test_a_format_check_that_fails_without_listing_files_is_never_a_pass(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("pyproject.toml", PYPROJECT + "[tool.ruff]\n")
    backend.on("ruff", lambda spec: (1, "some future output format\n"))

    result = box("Format Code")

    assert result.splitlines()[0] == "Format (ruff): FAILED"
    assert "file list was not recognised" in result and "some future output format" in result


@pytest.mark.usefixtures("everything_installed")
def test_unreadable_lint_output_is_an_error(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path)
    box.write("package.json", json.dumps({"devDependencies": {"eslint": "9"}}))
    backend.on("eslint", lambda spec: (2, "Oops! Something went wrong! :(\n"))

    result = box("Run Linter")

    assert result.splitlines()[0] == "Lint (eslint): ERROR"
    assert "Output unreadable" in result and "Oops! Something went wrong" in result


@pytest.mark.usefixtures("everything_installed")
def test_diagnostics_are_capped_and_counted(tmp_path: Path) -> None:
    _, backend, box = scripted(tmp_path, **{"tools.dev.max_diagnostics": 3})
    box.write("package.json", json.dumps({"devDependencies": {"eslint": "9"}}))
    entries = [
        {"ruleId": "r", "severity": 2, "message": f"problem {n}", "line": n, "column": 1}
        for n in range(1, 11)
    ]
    backend.on("eslint", lambda spec: (1, json.dumps([{"filePath": "a.js", "messages": entries}])))

    result = box("Run Linter")

    assert result.splitlines()[0] == "Lint (eslint): FAILED - 10 errors, 0 warnings"
    assert "a.js:3:1 error r: problem 3" in result and "problem 4" not in result
    assert "... 7 more not shown" in result
