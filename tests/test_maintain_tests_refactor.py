"""``maintain --task add-tests`` and ``--task refactor``: their policies, scopes, and reports."""

from __future__ import annotations

from pathlib import Path

import pytest
from maintain_helpers import check, event_types, git, latest, maintain, project, scripted, state_of
from pipeline_fakes import FakeRunner
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)

from engineering_team.modes import maintain as maintain_actions
from engineering_team.modes.change_report import SUMMARY_FILE
from engineering_team.modes.maintain_contracts import CoverageSnapshot
from engineering_team.pipeline.stages import StageRequest

pytestmark = pytest.mark.git
NEW_TEST = (
    "from app.store import add\n\n\n"
    "def test_add_appends_to_the_end():\n    assert add(['a'], 'b') == ['a', 'b']\n"
)


def fake_coverage(monkeypatch: pytest.MonkeyPatch, *percents: float) -> None:
    """The coverage tool is not installed everywhere: scripted snapshots, one per measurement."""

    queue = list(percents)

    def measure(_ctx: object) -> CoverageSnapshot:
        value = queue.pop(0)
        return CoverageSnapshot(
            status="measured", tool="fake-cov", covered=int(value), total=100, note=""
        )

    monkeypatch.setattr(maintain_actions, "measure_coverage", measure)


def test_add_tests_reports_a_coverage_delta_and_limits_the_agent_to_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    fake_coverage(monkeypatch, 40, 80)
    fake = FakeRunner(
        on_call=scripted(
            tests=lambda r: r.ctx.workspace.write_file("tests/test_extra.py", NEW_TEST)
        )
    )

    result, fake = maintain(repo, monkeypatch, "add-tests", "--goal", "cover the store", fake=fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.mode == "maintain" and ref.manifest.recipe == "add-tests"
    assert ref.manifest.verdict == "verified"
    assert [s.name for s in ref.manifest.stages] == [
        "profile", "baseline", "map", "coverage_before", "tests", "verify", "coverage_after",
        "review", "summary",
    ]  # fmt: skip
    state = state_of(ref)
    assert state.coverage is not None and state.coverage.change == 40.0
    assert check(state, "policy:tests_only").status == "passed"
    writer = next(r for r in fake.requests if r.stage.name == "tests")
    assert writer.teammate == "quality_engineer" and writer.write_scope is not None
    assert writer.write_scope.permits("tests/test_extra.py")
    assert not writer.write_scope.permits("app/store.py")
    summary = (ref.run_dir / SUMMARY_FILE).read_text()
    assert "## Coverage" in summary and "Before: 40.0%" in summary and "After: 80.0%" in summary
    assert "+40.0 percentage points" in summary
    assert "coverage.before" in event_types(ref) and "verify.policies" in event_types(ref)
    assert "Maintenance task: add-tests" in (ref.run_dir / "request.md").read_text()


def test_the_agent_is_told_where_the_tests_are_thinnest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.devtools.models import FileCoverage
    from engineering_team.pipeline.stages import CrewStageRunner

    repo = project(tmp_path)
    queue = [
        CoverageSnapshot(
            status="measured", tool="t", covered=10, total=100,
            least_covered=[FileCoverage(path="app/store.py", covered=1, total=10)],
        ),
        CoverageSnapshot(note="the tool broke"),
    ]  # fmt: skip
    monkeypatch.setattr(maintain_actions, "measure_coverage", lambda _ctx: queue.pop(0))

    result, fake = maintain(repo, monkeypatch, "add-tests")

    assert result.exit_code == 0, result.output
    request = next(r for r in fake.requests if r.stage.name == "tests")
    brief = CrewStageRunner._inputs(request)["coverage"]
    assert "Coverage before your work: 10.0%" in brief and "app/store.py: 10.0%" in brief
    summary = (latest().run_dir / SUMMARY_FILE).read_text()
    assert "After: unavailable. the tool broke" in summary and "Change:" not in summary


def test_without_a_coverage_tool_the_run_says_so_and_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    monkeypatch.setattr(
        maintain_actions,
        "measure_coverage",
        lambda _ctx: CoverageSnapshot(note=maintain_actions.NO_COVERAGE),
    )

    result, _ = maintain(repo, monkeypatch, "add-tests")

    assert result.exit_code == 0, result.output
    assert "No coverage tool was detected" in (latest().run_dir / SUMMARY_FILE).read_text()


def edit_code(request: StageRequest) -> None:
    request.ctx.workspace.write_file("app/store.py", "def add(items, text):\n    return [text]\n")


def test_a_test_task_that_touched_the_code_is_repaired_by_reverting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    fake_coverage(monkeypatch, 10, 20)

    def work(request: StageRequest) -> None:
        if request.stage.name == "tests":
            request.ctx.workspace.write_file("tests/test_extra.py", NEW_TEST)
            edit_code(request)  # a shell command got around the file tools' scope
        elif request.failures:  # the repair agent reverts what the policy lists
            assert "app/store.py (M): not a test file" in request.failures
            request.ctx.workspace.write_file("app/store.py", project_store())

    result, fake = maintain(repo, monkeypatch, "add-tests", fake=FakeRunner(on_call=work))

    assert result.exit_code == 0, result.output
    state = state_of(latest())
    assert state.verification.rounds == 1 and check(state, "policy:tests_only").status == "passed"
    repair = next(r for r in fake.requests if r.failures)
    assert repair.stage.name == "verify" and repair.teammate == "quality_engineer"
    assert repair.write_scope is not None and not repair.write_scope.permits("app/store.py")


def project_store() -> str:
    return "def add(items, text):\n    return [*items, text]\n"


def test_a_test_task_that_keeps_changing_the_code_fails_with_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    fake_coverage(monkeypatch, 10, 20)

    def work(request: StageRequest) -> None:
        if request.stage.name == "tests":
            edit_code(request)

    result, _ = maintain(repo, monkeypatch, "add-tests", fake=FakeRunner(on_call=work))

    assert result.exit_code == 3, result.output
    ref = latest()
    assert ref.manifest.verdict == "failed"
    assert check(state_of(ref), "policy:tests_only").status == "failed"
    assert (
        "app/store.py (M): not a test file"
        in (ref.run_dir / "reports" / "verification.md").read_text()
    )


# -- refactor -------------------------------------------------------------------------------


def refactor_store(request: StageRequest) -> None:
    request.ctx.workspace.write_file(
        "app/store.py",
        "def add(items, text):\n    result = [*items]\n"
        "    result.append(text)\n    return result\n",
    )


def test_a_refactor_that_keeps_the_tests_green_and_untouched_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    result, fake = maintain(
        repo, monkeypatch, "refactor", "--goal", "make add easier to read",
        fake=FakeRunner(on_call=scripted(refactor=refactor_store)),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    ref = latest()
    assert [s.name for s in ref.manifest.stages][:6] == [
        "profile", "baseline", "precondition", "map", "refactor", "verify",
    ]  # fmt: skip
    state = state_of(ref)
    for policy in ("tests_untouched", "manifests_untouched", "diff_size"):
        assert check(state, f"policy:{policy}").status == "passed"
    assert check(state, "tests").status == "passed"
    assert any(
        r.stage.name == "refactor" and r.teammate == "backend_engineer" for r in fake.requests
    )
    assert "make add easier to read" in (ref.run_dir / "request.md").read_text()


def test_a_refactor_that_edits_an_existing_test_fails_and_the_repair_is_told_to_revert_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    def work(request: StageRequest) -> None:
        if request.stage.name == "refactor":
            request.ctx.workspace.write_file("tests/test_store.py", "def test_add():\n    pass\n")

    result, fake = maintain(repo, monkeypatch, "refactor", fake=FakeRunner(on_call=work))

    assert result.exit_code == 3, result.output
    repair = next(r for r in fake.requests if r.failures)
    assert "tests/test_store.py: an existing test was changed" in repair.failures
    assert check(state_of(latest()), "policy:tests_untouched").status == "failed"


def test_a_refactor_bigger_than_the_limit_is_refused_by_the_diff_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[maintain]\nmax_refactor_lines = 10\n")

    def work(request: StageRequest) -> None:
        request.ctx.workspace.write_file(
            "app/big.py", "x = 1\n" * 30
        ) if request.stage.name == "refactor" else None

    result, _ = maintain(
        repo, monkeypatch, "refactor", "--config", str(config), fake=FakeRunner(on_call=work)
    )

    assert result.exit_code == 3, result.output
    report = (latest().run_dir / "reports" / "verification.md").read_text()
    assert "30 lines changed; a refactor may change at most 10" in report


def test_a_refactor_will_not_start_on_failing_tests_and_asks_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(
        tmp_path,
        {"tests/test_known_bug.py": "def test_broken():\n    assert False, 'known'\n"},
    )
    base = git(repo, "rev-parse", "HEAD")

    result, fake = maintain(repo, monkeypatch, "refactor", before=("--json",))

    assert result.exit_code == 4, result.output
    ref = latest()
    assert ref.manifest.verdict == "needs-info" and ref.manifest.status == "failed"
    assert state_of(ref).needs_info and "fix" in state_of(ref).needs_info[0]
    assert all(r.stage.name not in ("refactor", "verify") for r in fake.requests)
    assert git(repo, "diff", base, "--stat") == ""  # nothing of the project changed


def test_a_refactor_needs_tests_that_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = project(tmp_path)
    for name in ("tests/test_store.py",):
        (repo / name).unlink()
    git(repo, "commit", "-qam", "remove the tests")

    result, _ = maintain(repo, monkeypatch, "refactor")

    assert result.exit_code == 4, result.output
    assert "maintain --task add-tests" in " ".join(state_of(latest()).needs_info)
