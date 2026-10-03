"""Which checks a verification runs: user checks > the plan's commands > detected defaults."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from engineering_team.contracts import CheckSpec, Plan, ProjectCommands
from engineering_team.runtime.context import RunContext
from engineering_team.settings import load_settings
from engineering_team.verification.profiles import build_checks

MakeContext = Callable[..., RunContext]

PYPROJECT = (
    '[project]\nname = "calc"\nversion = "0.1.0"\n[build-system]\nrequires = ["hatchling"]\n'
    "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.mypy]\n"
)


def python_project(ctx: RunContext, directory: str = "") -> None:
    prefix = f"{directory}/" if directory else ""
    ctx.workspace.write_file(f"{prefix}pyproject.toml", PYPROJECT)
    ctx.workspace.write_file(f"{prefix}tests/test_calc.py", "def test_x():\n    assert True\n")


def plan(**commands: list[str]) -> Plan:
    return Plan(stack="python", commands=ProjectCommands(**commands))


def by_id(checks: list[CheckSpec]) -> dict[str, CheckSpec]:
    return {check.id: check for check in checks}


def user_check(check_id: str, kind: str, *argv: str) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        name=check_id,
        argv=list(argv),
        kind=kind,
        source="user",  # type: ignore[arg-type]
    )


def test_detected_defaults_cover_tests_lint_typecheck_and_build(make_context: MakeContext) -> None:
    ctx = make_context()
    python_project(ctx)

    result = build_checks(ctx, None, [])

    checks = by_id(result.checks)
    assert [c.id for c in result.checks] == ["tests", "lint", "typecheck", "build"]
    assert all(c.source == "detected" and c.cwd == "." and not c.argv for c in result.checks)
    # Tests and the build must pass; lint and type errors are reported but advisory by default.
    assert (checks["tests"].required, checks["build"].required) == (True, True)
    assert (checks["lint"].required, checks["typecheck"].required) == (False, False)
    assert [c.kind for c in result.checks] == ["test", "lint", "typecheck", "build"]


def test_static_checks_can_be_made_required(make_context: MakeContext) -> None:
    settings = load_settings().with_overrides({"verify.static_required": True}, source="test")
    ctx = make_context(settings=settings)
    python_project(ctx)

    checks = by_id(build_checks(ctx, None, []).checks)

    assert checks["lint"].required and checks["typecheck"].required


def test_the_plans_commands_beat_detected_defaults(make_context: MakeContext) -> None:
    ctx = make_context()
    python_project(ctx)

    result = build_checks(ctx, plan(test=["python -m pytest -q"], build=["uv build"]), [])

    checks = by_id(result.checks)
    assert checks["tests"].argv == ["python", "-m", "pytest", "-q"]
    assert checks["tests"].source == "plan" and checks["tests"].required
    assert checks["build"].argv == ["uv", "build"]
    assert checks["lint"].source == "detected"  # the plan declared no lint command


def test_user_checks_beat_the_plan_and_detection_for_their_kind(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    python_project(ctx)
    mine = user_check("mine", "test", "pytest", "-x")

    result = build_checks(ctx, plan(test=["python -m pytest"]), [mine, user_check("x", "custom")])

    assert [c.id for c in result.checks if c.kind == "test"] == ["mine"]
    assert "x" in by_id(result.checks)  # custom checks are added to, not instead of, the rest
    assert "build" in by_id(result.checks)


def test_a_plan_command_the_controller_may_not_run_falls_back_to_the_default(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    python_project(ctx)

    result = build_checks(ctx, plan(test=["cd app && pytest"]), [])

    check = by_id(result.checks)["tests"]
    assert check.source == "detected" and not check.argv
    assert any(
        "cd app && pytest" in note and "shell operator" in note.lower() for note in result.notes
    )


def test_a_project_with_nothing_to_run_still_has_a_required_tests_check(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("README.md", "# nothing to test\n")

    checks = build_checks(ctx, None, []).checks

    # An empty or untestable project must not be able to look verified.
    assert [(c.id, c.required) for c in checks] == [("tests", True)]


def test_each_project_of_a_monorepo_gets_its_own_checks(make_context: MakeContext) -> None:
    ctx = make_context()
    python_project(ctx, "backend")
    ctx.workspace.write_file(
        "web/package.json", '{"devDependencies": {"jest": "^29"}, "scripts": {"build": "x"}}'
    )

    checks = by_id(build_checks(ctx, None, []).checks)

    assert {"tests:backend", "tests:web", "build:backend", "build:web"} <= set(checks)
    assert checks["tests:web"].cwd == "web" and checks["tests:backend"].cwd == "backend"


def test_setup_and_smoke_come_from_the_plan_only(make_context: MakeContext) -> None:
    ctx = make_context()
    python_project(ctx)

    checks = by_id(build_checks(ctx, plan(setup=["uv sync"], run=["python -m calc"]), []).checks)

    assert (checks["setup"].kind, checks["setup"].required) == ("setup", False)
    assert (checks["smoke"].kind, checks["smoke"].required) == ("smoke", False)
    assert checks["smoke"].timeout == 10 and checks["smoke"].argv == ["python", "-m", "calc"]
    assert list(by_id(build_checks(ctx, None, []).checks)) == [
        "tests", "lint", "typecheck", "build"
    ]  # fmt: skip


def test_the_smoke_check_can_be_switched_off_or_required(make_context: MakeContext) -> None:
    off = load_settings().with_overrides({"verify.smoke": False}, source="test")
    ctx = make_context("off", settings=off)
    python_project(ctx)
    assert "smoke" not in by_id(build_checks(ctx, plan(run=["python -m calc"]), []).checks)

    strict = load_settings().with_overrides({"verify.smoke_required": True}, source="test")
    ctx = make_context("strict", settings=strict)
    python_project(ctx)
    assert by_id(build_checks(ctx, plan(run=["python -m calc"]), []).checks)["smoke"].required


def test_setup_runs_before_everything_else_and_user_checks_keep_their_order(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    python_project(ctx)
    users = [user_check("b", "custom", "true"), user_check("a", "custom", "true")]

    checks = build_checks(ctx, plan(setup=["uv sync"]), users).checks

    assert [c.id for c in checks] == ["setup", "tests", "lint", "typecheck", "build", "b", "a"]


@pytest.mark.parametrize("field", ["test", "build"])
def test_several_declared_commands_get_numbered_ids(make_context: MakeContext, field: str) -> None:
    ctx = make_context()
    python_project(ctx)

    checks = build_checks(ctx, plan(**{field: ["python -m a", "python -m b"]}), []).checks

    name = "tests" if field == "test" else "build"
    assert [c.id for c in checks if c.kind == field] == [name, f"{name}:2"]
