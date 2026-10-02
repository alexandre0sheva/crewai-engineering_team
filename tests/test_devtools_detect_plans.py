"""Stack detection and the command lines the plans build (pure; nothing runs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_team.devtools.detect import detect_stack, find_stacks
from engineering_team.devtools.models import TestFailure
from engineering_team.devtools.plans import Paths, Selection, build_test_command
from engineering_team.devtools.plans_deps import audit_command, coverage_command, install_argv
from engineering_team.devtools.plans_static import (
    build_argv,
    format_command,
    lint_command,
    typecheck_command,
)
from engineering_team.tools.support import ToolError


def project(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def paths(root: Path) -> Paths:
    return Paths(report=root / "scratch" / "report", directory=root / "scratch")


# -- detection ---------------------------------------------------------------------------


def test_a_python_project_with_pytest_ruff_and_mypy(tmp_path: Path) -> None:
    project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="x"\n[tool.pytest.ini_options]\n[tool.ruff]\n'
            '[tool.mypy]\n[build-system]\nrequires=["hatchling"]\n',
            "uv.lock": "",
        },
    )

    stack = detect_stack(tmp_path)

    assert stack is not None
    assert (stack.language, stack.manager, stack.test) == ("python", "uv", "pytest")
    assert (stack.lint, stack.typecheck, stack.format) == ("ruff", "mypy", "ruff")
    assert stack.build == ("uv", "build")


def test_python_falls_back_to_unittest_or_pytest_from_the_tests_themselves(tmp_path: Path) -> None:
    project(tmp_path / "a", {"test_x.py": "import unittest\n"})
    project(tmp_path / "b", {"test_x.py": "def test_x():\n    pass\n"})
    project(tmp_path / "c", {"requirements.txt": "flask\n"})

    assert (detect_stack(tmp_path / "a") or pytest.fail()).test == "unittest"
    assert (detect_stack(tmp_path / "b") or pytest.fail()).test == "pytest"
    assert (detect_stack(tmp_path / "c") or pytest.fail()).test is None


def test_a_typescript_project(tmp_path: Path) -> None:
    package = {
        "scripts": {"build": "tsc", "test": "vitest run"},
        "devDependencies": {"vitest": "^2", "eslint": "^9", "prettier": "^3", "typescript": "^5"},
    }
    project(
        tmp_path, {"package.json": json.dumps(package), "pnpm-lock.yaml": "", "tsconfig.json": "{}"}
    )

    stack = detect_stack(tmp_path) or pytest.fail()

    assert (stack.language, stack.manager, stack.test) == ("typescript", "pnpm", "vitest")
    assert (stack.lint, stack.typecheck, stack.format) == ("eslint", "tsc", "prettier")
    assert stack.build == ("pnpm", "run", "build") and stack.audit == "pnpm"


def test_a_js_project_with_an_unknown_test_runner_says_so(tmp_path: Path) -> None:
    project(tmp_path, {"package.json": json.dumps({"scripts": {"test": "mocha"}})})

    stack = detect_stack(tmp_path) or pytest.fail()

    assert stack.test is None and "unrecognised framework" in stack.notes[0]


@pytest.mark.parametrize(
    ("files", "language", "manager", "test"),
    [
        ({"go.mod": "module example.com/x\n"}, "go", "go", "go"),
        ({"Cargo.toml": "[package]\nname='x'\n"}, "rust", "cargo", "cargo"),
        ({"pom.xml": "<project/>"}, "java", "maven", "maven"),
        ({"build.gradle.kts": ""}, "java", "gradle", "gradle"),
        ({"App.csproj": "<Project/>"}, "csharp", "dotnet", "dotnet"),
        ({"Gemfile": "gem 'rspec'\n"}, "ruby", "bundler", "rspec"),
        ({"Gemfile": "", "test/a_test.rb": ""}, "ruby", "bundler", "minitest"),
        (
            {"composer.json": '{"require-dev": {"phpunit/phpunit": "^11"}}'},
            "php",
            "composer",
            "phpunit",
        ),
    ],
)
def test_other_languages(
    tmp_path: Path, files: dict[str, str], language: str, manager: str, test: str
) -> None:
    project(tmp_path, files)

    stack = detect_stack(tmp_path) or pytest.fail()

    assert (stack.language, stack.manager, stack.test) == (language, manager, test)


def test_an_empty_directory_has_no_stack_and_find_stacks_looks_in_subfolders(
    tmp_path: Path,
) -> None:
    project(
        tmp_path,
        {
            "backend/pyproject.toml": "[project]\nname='b'\n",
            "web/package.json": "{}",
            "node_modules/dep/package.json": "{}",
            ".hidden/package.json": "{}",
        },
    )

    assert detect_stack(tmp_path) is None
    found = find_stacks(tmp_path)

    assert [(s.directory, s.language) for s in found] == [
        ("backend", "python"),
        ("web", "javascript"),
    ]


# -- commands ----------------------------------------------------------------------------


def test_pytest_command_has_a_junit_report_and_the_selection(tmp_path: Path) -> None:
    stack = detect_stack(project(tmp_path, {"pyproject.toml": "[tool.pytest.ini_options]\n"}))
    assert stack is not None
    selection = Selection(("tests/a.py",), filter="login", markers="slow", fail_fast=True)

    plan = build_test_command(stack, tmp_path, selection, paths(tmp_path))

    assert plan.argv[-6:] == ["-x", "-k", "login", "-m", "slow", "tests/a.py"]
    assert f"--junitxml={tmp_path / 'scratch' / 'report'}" in plan.argv
    assert plan.argv[1:3] == ["-m", "pytest"]


def test_reruns_pass_exact_ids_for_each_framework(tmp_path: Path) -> None:
    def failure(test_id: str, file: str | None = None) -> TestFailure:
        return TestFailure(test_id=test_id, file=file)

    py = (
        detect_stack(project(tmp_path / "py", {"pyproject.toml": "[tool.pytest]\n"}))
        or pytest.fail()
    )
    go = detect_stack(project(tmp_path / "go", {"go.mod": "module m\n"})) or pytest.fail()
    cargo = detect_stack(project(tmp_path / "rs", {"Cargo.toml": "[package]\n"})) or pytest.fail()
    mvn = detect_stack(project(tmp_path / "mvn", {"pom.xml": "<project/>"})) or pytest.fail()
    js = (
        detect_stack(
            project(
                tmp_path / "js", {"package.json": json.dumps({"devDependencies": {"jest": "1"}})}
            )
        )
        or pytest.fail()
    )

    def argv(stack, root: str, *failures: TestFailure) -> list[str]:  # type: ignore[no-untyped-def]
        sel = Selection(failures=failures)
        return build_test_command(stack, tmp_path / root, sel, paths(tmp_path)).argv

    assert argv(py, "py", failure("t/a.py::test_x"))[-1] == "t/a.py::test_x"
    go_argv = argv(go, "go", failure("m/pkg::TestA/case"), failure("m/pkg::TestB"))
    assert go_argv[go_argv.index("-run") + 1] == "^(TestA|TestB)$" and go_argv[-1] == "m/pkg"
    assert argv(cargo, "rs", failure("tests::a"))[-3:] == ["--", "--exact", "tests::a"]
    assert "-Dtest=C#m1,C#m2" in argv(mvn, "mvn", failure("C#m1"), failure("C#m2"))
    js_argv = argv(js, "js", failure("src/a.test.js::adds (1+1)"))
    assert (
        js_argv[js_argv.index("-t") + 1] == r"^(adds\ \(1\+1\))$" and js_argv[-1] == "src/a.test.js"
    )


def test_no_framework_is_a_tool_error_listing_the_options(tmp_path: Path) -> None:
    stack = detect_stack(project(tmp_path, {"requirements.txt": "flask\n"})) or pytest.fail()

    with pytest.raises(ToolError, match="Pass framework= one of"):
        build_test_command(stack, tmp_path, Selection(), paths(tmp_path))


def test_static_tool_commands(tmp_path: Path) -> None:
    ruff = lint_command("ruff", tmp_path, [], "r").argv
    assert ruff[-5:] == ["check", "--output-format", "json", "--no-fix", "."]
    eslint = lint_command("eslint", tmp_path, ["src"], "r").argv
    assert eslint == ["npx", "--no-install", "eslint", "-f", "json", "src"]
    assert typecheck_command("tsc", tmp_path, [], "r").argv[:3] == ["npx", "--no-install", "tsc"]
    assert format_command("gofmt", tmp_path, [], write=False, root="r").argv == ["gofmt", "-l", "."]
    assert format_command("gofmt", tmp_path, [], write=True, root="r").argv == ["gofmt", "-w", "."]
    assert format_command("rustfmt", tmp_path, [], write=False, root="r").argv[-2:] == [
        "--",
        "--check",
    ]
    for builder in (lint_command, typecheck_command):
        with pytest.raises(ToolError, match="Supported:"):
            builder("nope", tmp_path, [], "r")
    with pytest.raises(ToolError, match="Supported:"):
        format_command("nope", tmp_path, [], write=False, root="r")


def test_build_argv_needs_a_detected_build(tmp_path: Path) -> None:
    bare = detect_stack(project(tmp_path / "a", {"requirements.txt": "x\n"})) or pytest.fail()
    cargo = detect_stack(project(tmp_path / "b", {"Cargo.toml": "[package]\n"})) or pytest.fail()

    with pytest.raises(ToolError, match="No build command"):
        build_argv(bare)
    assert build_argv(cargo) == ["cargo", "build", "--message-format=json"]


def test_install_commands_prefer_lockfiles(tmp_path: Path) -> None:
    def stack(name: str, files: dict[str, str]):  # type: ignore[no-untyped-def]
        root = project(tmp_path / name, files)
        return detect_stack(root) or pytest.fail(), root

    npm, npm_root = stack("npm", {"package.json": "{}", "package-lock.json": "{}"})
    assert install_argv(npm, npm_root) == ["npm", "ci"]
    uv, uv_root = stack("uv", {"pyproject.toml": "[project]\n", "uv.lock": ""})
    assert install_argv(uv, uv_root) == ["uv", "sync"]
    pip, pip_root = stack("pip", {"requirements.txt": "x\n"})
    assert install_argv(pip, pip_root)[-3:] == ["install", "-r", "requirements.txt"]
    go, go_root = stack("go", {"go.mod": "module m\n"})
    assert install_argv(go, go_root) == ["go", "mod", "download"]
    bare, bare_root = stack("bare", {"x.py": ""})
    with pytest.raises(ToolError, match="declares no dependencies"):
        install_argv(bare, bare_root)


def test_audit_and_coverage_support_only_what_they_can_parse(tmp_path: Path) -> None:
    java = detect_stack(project(tmp_path / "j", {"pom.xml": "<project/>"})) or pytest.fail()
    py = (
        detect_stack(project(tmp_path / "p", {"pyproject.toml": "[tool.pytest]\n"}))
        or pytest.fail()
    )

    with pytest.raises(ToolError, match="supports pip-audit"):
        audit_command(java, tmp_path / "j")
    with pytest.raises(ToolError, match="Coverage is supported"):
        coverage_command(java, tmp_path / "j", Selection(), paths(tmp_path), "r")
    plan = coverage_command(py, tmp_path / "p", Selection(), paths(tmp_path), "r")
    assert plan.tool == "coverage.py"
    assert plan.tests.argv[1:4] == ["-m", "coverage", "run"] and "pytest" in plan.tests.argv
    assert plan.follow_up is not None and plan.follow_up[-2] == "-o"
