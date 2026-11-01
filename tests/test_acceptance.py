"""Acceptance tests first: written apart from the implementers, proven red on the baseline, and
the criteria they cover verified once they pass."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner
from pipeline_fakes import FakeRunner
from repo_fixtures import make_repo
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from test_feature_mode import LEGACY, PLAN, WORKSPACE_ROOT, latest, state_of
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import (
    AcceptanceCriterion,
    CheckResult,
    Spec,
    TaskProfile,
    TaskTarget,
)
from engineering_team.modes.acceptance_contracts import AcceptanceSuite, AcceptanceTest
from engineering_team.modes.acceptance_red import command_problem, file_problem, red_problem
from engineering_team.modes.change_report import SUMMARY_FILE
from engineering_team.pipeline.compose import compose
from engineering_team.pipeline.conditions import CONDITIONS
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.tools import build_tools

pytestmark = [pytest.mark.git, pytest.mark.usefixtures("full_pipeline")]
runner = CliRunner()
REQUEST = "Add a search function that finds notes by text."
SPEC = Spec(
    title="Search notes",
    criteria=[
        AcceptanceCriterion(id="AC-1", text="search returns the notes that contain the text"),
        AcceptanceCriterion(id="AC-2", text="the search is fast enough to feel instant"),
    ],
)
ACCEPTANCE_FILE = "tests/test_search_acceptance.py"
# Red on the baseline because ``search`` does not exist yet: the import fails (pytest exit 2).
ACCEPTANCE = (
    "from app.store import search\n\n\n"
    "def test_search_returns_the_matching_notes():  # AC-1\n"
    "    assert search(['apple', 'pear', 'grape'], 'ap') == ['apple', 'grape']\n"
)
PASSING_FILE = "tests/test_add_acceptance.py"
PASSING = "from app.store import add\n\n\ndef test_add():\n    assert add([], 'a') == ['a']\n"
SEARCH = (
    "def add(items, text):\n    return [*items, text]\n\n\n"
    "def search(items, text):\n    return [item for item in items if text in item]\n"
)
GOOD = AcceptanceTest(
    criteria=["AC-1"],
    files=[ACCEPTANCE_FILE],
    command=f"python -m pytest {ACCEPTANCE_FILE}",
    expected_failure="ImportError: cannot import name 'search'",
)
ALREADY_PASSES = AcceptanceTest(
    criteria=["AC-1"], files=[PASSING_FILE], command=f"python -m pytest {PASSING_FILE}"
)
UNTESTABLE = ["AC-2: how fast feels instant needs a person"]
# The implementers' own test: it mentions AC-2, which makes AC-2 `referenced` and nothing more.
OWN_TEST = "def test_search_is_quick():  # AC-2\n    assert True\n"


def script(
    files: list[tuple[str, str]], *, implement: Callable[[StageRequest], None] | None = None
) -> Callable[[StageRequest], None]:
    """The scripted team: the quality engineer writes one file per acceptance attempt (the last
    repeats), the implementer adds ``search``, the tests stage adds the implementers' own test."""

    made = {"acceptance": 0}

    def on_call(request: StageRequest) -> None:
        write = request.ctx.workspace.write_file
        if request.stage.name == "acceptance":
            name, text = files[min(made["acceptance"], len(files) - 1)]
            made["acceptance"] += 1
            write(name, text)
        elif request.stage.name == "implement" and request.package is not None:
            write("app/store.py", SEARCH)
            if implement is not None:
                implement(request)
        elif request.stage.name == "tests":
            write("tests/test_search_own.py", OWN_TEST)

    return on_call


def run_feature(
    repo: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeRunner, *extra: str
) -> tuple[Any, FakeRunner]:
    use_runner(monkeypatch, fake)
    command = ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT), "feature", "--repo"]
    result = runner.invoke(app, [*command, str(repo), "--request", REQUEST, *extra])
    return result, fake


def feature(on_call: Callable[[StageRequest], None], *suites: AcceptanceSuite) -> FakeRunner:
    return FakeRunner(plan=PLAN, specs=[SPEC], on_call=on_call, acceptance=list(suites))


def events(ref: Any) -> list[dict[str, Any]]:
    lines = (ref.run_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


# -- the whole run ------------------------------------------------------------------------------


def test_criteria_are_verified_by_acceptance_tests_that_were_red_on_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    refused: list[str] = []

    def try_to_edit_the_test(request: StageRequest) -> None:
        tools = build_tools(request.ctx, write_scope=request.write_scope, lazy=False)
        write = next(tool for tool in tools if tool.name == "Write Project File")
        refused.append(str(write.run(path=ACCEPTANCE_FILE, content="def test_x(): pass\n")))

    fake = feature(
        script([(ACCEPTANCE_FILE, ACCEPTANCE)], implement=try_to_edit_the_test),
        AcceptanceSuite(tests=[GOOD], untestable=UNTESTABLE),
    )

    result, _ = run_feature(repo, monkeypatch, fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.verdict == "verified"
    stages = [s.name for s in ref.manifest.stages if s.status == "succeeded"]
    assert stages.index("acceptance") == stages.index("impact") + 1
    assert stages.index("implement") == stages.index("acceptance") + 1
    record = state_of(ref).acceptance_record
    assert record is not None and record.attempts == 1 and record.rejected == []
    [test] = record.accepted
    assert test.id == "acceptance-1" and test.criteria == ["AC-1"]
    assert test.red.status == "failed" and test.red.exit_code == 2  # the import of `search`
    assert test.green is not None and test.green.status == "passed"
    assert record.untestable == UNTESTABLE
    # The criteria: AC-1 proven by the test that was red first; AC-2 only mentioned by the
    # implementers' own test, which proves nothing.
    coverage = {c.id: c for c in state_of(ref).verification.coverage}
    assert coverage["AC-1"].status == "verified" and coverage["AC-1"].checks == ["acceptance-1"]
    assert "red on the baseline" in coverage["AC-1"].note
    assert coverage["AC-2"].status == "referenced"
    assert "tests/test_search_own.py" in coverage["AC-2"].note
    # Nobody but the quality engineer could write the test: the write tool refused it.
    assert refused and refused[0].startswith("ERROR:") and "outside your write scope" in refused[0]
    report = (ref.run_dir / "reports" / "verification.md").read_text()
    assert "## Acceptance tests (written apart, red on the baseline)" in report
    assert "| acceptance-1 | AC-1 | failed, exit 2 | passed |" in report
    assert "## Acceptance tests" in (ref.run_dir / SUMMARY_FILE).read_text()
    kinds = [e["type"] for e in events(ref) if e["type"].startswith("acceptance.")]
    assert kinds == ["acceptance.red", "acceptance.green"]


def test_the_acceptance_stage_writes_tests_only_and_implementers_cannot_write_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    fake = feature(
        script([(ACCEPTANCE_FILE, ACCEPTANCE)]),
        AcceptanceSuite(tests=[GOOD], untestable=UNTESTABLE),
    )

    result, _ = run_feature(repo, monkeypatch, fake)

    assert result.exit_code == 0, result.output
    scopes = {
        (r.stage.name, r.package.id if r.package else None): r.write_scope for r in fake.requests
    }
    acceptance = scopes[("acceptance", None)]
    assert acceptance is not None and acceptance.permits(ACCEPTANCE_FILE)
    assert not acceptance.permits("app/store.py")
    for key in (("implement", "WP-1"), ("tests", None)):
        scope = scopes[key]
        assert scope is not None and not scope.permits(ACCEPTANCE_FILE), key
        assert scope.permits("app/store.py"), key
    # The implementers are told which tests they must make pass.
    implement = next(r for r in fake.requests if r.stage.name == "implement")
    assert f"acceptance-1 (AC-1): `python -m pytest {ACCEPTANCE_FILE}`" in implement.handoff


def test_a_test_passing_on_the_baseline_is_rejected_and_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    fake = feature(
        script([(PASSING_FILE, PASSING), (ACCEPTANCE_FILE, ACCEPTANCE)]),
        AcceptanceSuite(tests=[ALREADY_PASSES], untestable=UNTESTABLE),
        AcceptanceSuite(tests=[GOOD], untestable=UNTESTABLE),
    )

    result, _ = run_feature(repo, monkeypatch, fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    record = state_of(ref).acceptance_record
    assert record is not None and record.attempts == 2
    assert [t.files for t in record.accepted] == [[ACCEPTANCE_FILE]]
    assert len(record.rejected) == 1 and "passes on the baseline" in record.rejected[0]
    second = [r for r in fake.requests if r.stage.name == "acceptance"][1]
    assert "Rejected" in second.note and "passes on the baseline" in second.note
    # The rejected test's file was the stage's own: it is gone, and nothing pins it.
    assert not (Path(ref.workspace) / PASSING_FILE).exists()
    assert PASSING_FILE not in record.digests and ACCEPTANCE_FILE in record.digests


def test_a_test_still_wrong_after_the_attempts_is_dropped_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    fake = feature(
        script([(PASSING_FILE, PASSING)]),
        AcceptanceSuite(tests=[ALREADY_PASSES], untestable=UNTESTABLE),
    )

    result, _ = run_feature(repo, monkeypatch, fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    state = state_of(ref)
    assert state.acceptance_record is not None and state.acceptance_record.accepted == []
    assert len(state.acceptance_record.rejected) == 2  # max_attempts = 2
    assert not (Path(ref.workspace) / PASSING_FILE).exists()
    assert not any(c.id.startswith("acceptance-") for c in state.checks)
    assert {c.id: c.status for c in state.verification.coverage}["AC-1"] != "verified"
    assert "acceptance.none" in [e["type"] for e in events(ref)]


def test_an_acceptance_test_edited_after_it_was_red_fails_the_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)

    def weaken(request: StageRequest) -> None:  # around the write scope, as a command could
        request.ctx.workspace.write_file(ACCEPTANCE_FILE, "def test_nothing():\n    pass\n")

    fake = feature(
        script([(ACCEPTANCE_FILE, ACCEPTANCE)], implement=weaken),
        AcceptanceSuite(tests=[GOOD], untestable=UNTESTABLE),
    )

    result, _ = run_feature(repo, monkeypatch, fake)

    assert result.exit_code == 3, result.output
    ref = latest()
    state = state_of(ref)
    assert state.verification.verdict == "failed"
    assert any("acceptance test file(s)" in p for p in state.verification.problems)
    assert state.acceptance_record is not None
    assert state.acceptance_record.accepted[0].green is None


def test_no_acceptance_skips_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path / "legacy", LEGACY)
    fake = feature(script([(ACCEPTANCE_FILE, ACCEPTANCE)]), AcceptanceSuite(tests=[GOOD]))

    result, _ = run_feature(repo, monkeypatch, fake, "--no-acceptance")

    assert result.exit_code == 0, result.output
    ref = latest()
    skipped = next(s for s in ref.manifest.stages if s.name == "acceptance")
    assert skipped.status == "skipped" and "no_acceptance" in skipped.detail
    assert "acceptance" not in [stage for stage, _ in fake.calls]
    assert state_of(ref).acceptance_record is None


# -- the composer and the skip conditions -------------------------------------------------------


def profile(size: str) -> TaskProfile:
    return TaskProfile.model_validate(
        {"intents": ["feature"], "target": TaskTarget(kind="existing"), "size": size}
    )


def test_a_feature_gets_the_acceptance_stage_before_implement_unless_it_is_trivial() -> None:
    names = [s.name for s in compose(profile("small")).recipe.stages]

    assert names.index("acceptance") == names.index("implement") - 1
    assert "acceptance" not in [s.name for s in compose(profile("trivial")).recipe.stages]
    bugfix = TaskProfile.model_validate(
        {"intents": ["bugfix"], "target": TaskTarget(kind="existing"), "size": "medium"}
    )
    assert "acceptance" not in [s.name for s in compose(bugfix).recipe.stages]


def test_an_escalated_rung_or_no_acceptance_skips_the_stage() -> None:
    from engineering_team.settings import Settings

    settings = Settings()
    fresh = PipelineState()

    assert not CONDITIONS["escalated"](fresh, settings)
    assert not CONDITIONS["no_acceptance"](fresh, settings)
    assert CONDITIONS["escalated"](PipelineState(escalation="The solo rung failed."), settings)
    assert CONDITIONS["no_acceptance"](PipelineState(options={"no_acceptance": True}), settings)


# -- the red-on-baseline rule -------------------------------------------------------------------

ARGV = ["python", "-m", "pytest", "tests/test_search_acceptance.py"]


def result(status: str = "failed", exit_code: int | None = 1, tail: str = "") -> CheckResult:
    return CheckResult.model_validate(
        {"id": "acceptance-1", "status": status, "exit_code": exit_code, "log_tail": tail}
    )


def project(name: str) -> bool:
    return name.split(".")[0] == "app"


@pytest.mark.parametrize(
    ("check", "reason"),
    [
        (result(tail="E   assert ['b'] == ['a', 'b']\n1 failed"), None),
        (
            result(exit_code=2, tail="ImportError: cannot import name 'search' from 'app.store'"),
            None,
        ),
        (result(exit_code=2, tail="ModuleNotFoundError: No module named 'app.search'"), None),
        (result("passed", 0, "1 passed"), "passes on the baseline"),
        (result(exit_code=2, tail='  File "t.py", line 3\nSyntaxError: invalid syntax'), "compile"),
        (result(tail="E       fixture 'notes_db' not found"), "fixture"),
        (
            result(exit_code=2, tail="ModuleNotFoundError: No module named 'requests_mock'"),
            "dependency",
        ),
        (result(exit_code=2, tail="ERROR collecting: duplicate test names"), "could not collect"),
        (result(exit_code=5, tail="no tests ran"), "found no test"),
        (result(exit_code=127, tail="python: not found"), "could not be started"),
        (result("unavailable", None), "could not run"),
    ],
)
def test_only_a_failure_for_the_right_reason_counts_as_red(
    check: CheckResult, reason: str | None
) -> None:
    why = red_problem(ARGV, check, known_module=project)

    assert why is None if reason is None else (why is not None and reason in why), why


def test_a_test_must_run_alone_and_live_in_new_test_files() -> None:
    files = ["tests/test_search_acceptance.py"]

    assert command_problem(ARGV, files) is None
    assert (
        command_problem(
            ["python", "-m", "pytest", "tests/test_search_acceptance.py::test_x"], files
        )
        is None
    )
    assert "whole suite" in (command_problem(["python", "-m", "pytest"], files) or "")
    assert "whole suite" in (command_problem(["python", "-m", "pytest", "tests"], files) or "")
    assert (
        command_problem(["go", "test", "./search", "-run", "TestX"], ["search/x_test.go"]) is None
    )
    assert file_problem(files, set(), lambda _: True) == ""
    assert "existing" in file_problem(files, set(files), lambda _: True)
    assert "not test files" in file_problem(["app/store.py"], set(), lambda _: True)
    assert "do not exist" in file_problem(files, set(), lambda _: False)
