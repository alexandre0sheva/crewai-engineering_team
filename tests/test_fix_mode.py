"""``engineering-team fix``: a bug in an existing project, red before the fix and green after."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner
from pipeline_fakes import FakeRunner
from repo_fixtures import PYTHON_APP, make_repo, snapshot
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from test_feature_mode import git, latest, state_of
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import RunVerdict
from engineering_team.main import exit_code_for, failure_line
from engineering_team.modes.change_report import PATCH_FILE, SUMMARY_FILE
from engineering_team.modes.fix_contracts import FixNote, Repro
from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
from engineering_team.pipeline.state import RunResult
from engineering_team.runtime.run_store import RunStore

pytestmark = pytest.mark.git
runner = CliRunner()
WORKSPACE_ROOT = "ws"
REPORT = "Adding a note drops the notes that were already there."

# The seeded bug: ``add`` forgets the notes it was given. The project's one test still passes;
# ``check_bug.py`` is a script of the person's that shows the bug.
BUGGY = {
    **PYTHON_APP,
    "app/store.py": "def add(items, text):\n    return [text]\n",
    "check_bug.py": (
        "from app.store import add\n\n"
        "assert add(['a'], 'b') == ['a', 'b'], 'add dropped the earlier note'\n"
    ),
}
FIXED = "def add(items, text):\n    return [*items, text]\n"
REGRESSION = (
    "from app.store import add\n\n\n"
    "def test_add_keeps_the_earlier_notes():\n    assert add(['a'], 'b') == ['a', 'b']\n"
)
PASSING = "def test_nothing_is_wrong():\n    assert True\n"
GOOD = Repro(
    command="python -m pytest tests/test_regression.py",
    files=["tests/test_regression.py"],
    expected_failure="assert ['b'] == ['a', 'b']",
    summary="add drops the earlier notes",
)
NOT_RED = Repro(
    command="python -m pytest tests/test_passes.py", files=["tests/test_passes.py"], summary="x"
)
TRACE = """\
Traceback (most recent call last):
  File "/home/dev/notes/app/main.py", line 9, in main
    print(add(load(), parser.parse_args().text))
  File "/home/dev/notes/app/store.py", line 2, in add
    return items[0] + [text]
IndexError: list index out of range
"""


def work(
    on_fix: Callable[[StageRequest], None] | None = None,
    *,
    reproductions: list[tuple[str, str]] | None = None,
) -> Callable[[StageRequest], None]:
    """What the scripted debugger does: write the failing test (one file per attempt; the last
    repeats), then the fix."""

    attempts = reproductions or [("tests/test_regression.py", REGRESSION)]
    made = {"reproduce": 0}

    def on_call(request: StageRequest) -> None:
        write = request.ctx.workspace.write_file
        if request.stage.name == "reproduce":
            name, text = attempts[min(made["reproduce"], len(attempts) - 1)]
            made["reproduce"] += 1
            write(name, text)
        elif request.stage.name == "fix":
            write("app/store.py", FIXED)
            if on_fix is not None:
                on_fix(request)

    return on_call


def fix_runner(on_call: Callable[[StageRequest], None] | None = None, **options: Any) -> FakeRunner:
    options.setdefault("repros", [GOOD])
    options.setdefault(
        "fix_note",
        FixNote(
            root_cause="add built a one-element list instead of extending the one it was given.",
            change="add returns the old items followed by the new one.",
            risk="Callers that relied on the old (wrong) result.",
            files=["app/store.py"],
        ),
    )
    return FakeRunner(on_call=on_call or work(), **options)


def run_fix(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    fake: FakeRunner | None = None,
    report: str | None = REPORT,
    before: tuple[str, ...] = (),
) -> tuple[Any, FakeRunner]:
    scripted = use_runner(monkeypatch, fake or fix_runner())
    command = ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT), *before, "fix"]
    command += ["--repo", str(repo)]
    if report is not None:
        command += ["--request", report]
    result = runner.invoke(app, [*command, *extra])
    return result, scripted


def event_types(ref: Any) -> list[str]:
    lines = (ref.run_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line)["type"] for line in lines]


# -- the whole run ----------------------------------------------------------------------------


def test_the_seeded_bug_is_fixed_with_a_regression_test_and_the_run_proves_red_then_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    base = git(repo, "rev-parse", "HEAD")
    seen: list[tuple[Any, Any, Any]] = []

    def at_fix(request: StageRequest) -> None:
        record = request.state.fix
        assert record is not None
        seen.append((record.reproduced, record.red and record.red.status, record.green))

    result, fake = run_fix(repo, monkeypatch, fake=fix_runner(work(at_fix)))

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.mode == "fix" and ref.manifest.recipe == "fix"
    assert ref.manifest.status == "succeeded" and ref.manifest.verdict == "verified"
    assert [s.name for s in ref.manifest.stages] == [
        "profile", "baseline", "map", "triage", "reproduce", "fix", "verify", "review", "summary",
    ]  # fmt: skip
    # Red was seen by the controller before the fix agent worked; green only after it.
    assert seen == [(True, "failed", None)]
    state = state_of(ref)
    assert state.fix is not None and state.fix.reproduced
    assert state.fix.red is not None and state.fix.red.exit_code == 1
    assert state.fix.green is not None and state.fix.green.status == "passed"
    assert state.fix.green.exit_code == 0 and state.fix.green.revision != state.fix.red.revision
    types = event_types(ref)
    assert types.index("fix.red") < types.index("fix.green")
    repro_check = next(c for c in state.checks if c.id == "repro")
    assert repro_check.status == "passed" and repro_check.required
    # The regression test is part of the branch; the patch carries it and the fix.
    assert "test_add_keeps_the_earlier_notes" in git(repo, "show", "HEAD:tests/test_regression.py")
    assert git(repo, "show", "HEAD:app/store.py") + "\n" == FIXED
    assert_patch_applies_fix(repo, base, ref.run_dir / PATCH_FILE, tmp_path)
    summary = (ref.run_dir / SUMMARY_FILE).read_text()
    assert "## The fix" in summary and "Reproduced: **yes**" in summary
    assert "Red (before the fix)" in summary and "Green (after the fix)" in summary
    assert "`tests/test_regression.py`" in summary
    assert "add built a one-element list" in summary and "Callers that relied" in summary
    assert "not evidence" in summary
    assert (ref.run_dir / "verification" / "repro-red-1.json").is_file()
    assert any(r.stage.name == "fix" and r.teammate == "debugger" for r in fake.requests)


def assert_patch_applies_fix(repo: Path, base: str, patch: Path, tmp: Path) -> None:
    clone = tmp / "fresh-clone"
    git(tmp, "clone", "-q", "--no-hardlinks", str(repo), str(clone))
    git(clone, "checkout", "-q", base)
    git(clone, "apply", str(patch))
    assert (clone / "tests" / "test_regression.py").is_file()
    assert (clone / "app" / "store.py").read_text() == FIXED


def test_a_fix_cannot_edit_the_reproduction_and_an_edit_anyway_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)

    def weaken(request: StageRequest) -> None:  # the "fix" makes the test pass by gutting it
        request.ctx.workspace.write_file(
            "tests/test_regression.py", "def test_add_keeps_the_earlier_notes():\n    pass\n"
        )

    result, fake = run_fix(repo, monkeypatch, fake=fix_runner(work(weaken)))

    assert result.exit_code == 3, result.output
    ref = latest()
    assert ref.manifest.verdict == "failed"
    reproduce = next(r for r in fake.requests if r.stage.name == "reproduce")
    fix = next(r for r in fake.requests if r.stage.name == "fix")
    assert reproduce.write_scope is None  # it writes the reproduction
    assert fix.write_scope is not None
    assert fix.write_scope.permits("app/store.py")
    assert not fix.write_scope.permits("tests/test_regression.py")
    report = (ref.run_dir / "reports" / "verification.md").read_text()
    assert "changed after they were seen failing" in report
    record = state_of(ref).fix
    assert record is not None and record.green is None


def test_a_fix_that_does_not_fix_the_bug_is_repaired_until_the_reproduction_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)

    def half_fix(request: StageRequest) -> None:
        if request.stage.name == "fix":
            request.ctx.workspace.write_file(
                "app/store.py", "def add(items, text):\n    return []\n"
            )
        elif request.failures:  # the repair round of the verify stage
            request.ctx.workspace.write_file("app/store.py", FIXED)

    def script(request: StageRequest) -> None:
        if request.stage.name == "reproduce":
            request.ctx.workspace.write_file("tests/test_regression.py", REGRESSION)
        else:
            half_fix(request)

    result, fake = run_fix(repo, monkeypatch, fake=fix_runner(script))

    assert result.exit_code == 0, result.output
    state = state_of(latest())
    assert state.verification.rounds >= 1 and state.fix is not None
    assert state.fix.green is not None
    repair = next(r for r in fake.requests if r.failures)
    assert "check `repro`" in repair.failures and repair.stage.name == "verify"


# -- reproduction -----------------------------------------------------------------------------


def test_a_reproduction_that_passes_is_sent_back_and_a_later_one_that_fails_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    first_passes = [("tests/test_passes.py", PASSING), ("tests/test_regression.py", REGRESSION)]
    fake = fix_runner(work(reproductions=first_passes), repros=[NOT_RED, GOOD])

    result, scripted = run_fix(repo, monkeypatch, fake=fake)

    assert result.exit_code == 0, result.output
    state = state_of(latest())
    assert state.fix is not None and state.fix.attempts == 2 and state.fix.reproduced
    assert state.fix.command == GOOD.command
    reproduce = [r for r in scripted.requests if r.stage.name == "reproduce"]
    assert len(reproduce) == 2 and reproduce[0].note == ""
    assert "did not reproduce the bug" in reproduce[1].note
    assert "the reproduction passed" in reproduce[1].note
    assert "fix.not_red" in event_types(latest())


def test_a_bug_that_cannot_be_reproduced_stops_with_questions_and_exit_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    before = snapshot(repo)
    asking = Repro(
        questions=["Which Python version do you use?", "What input did you type?"],
    )
    fake = fix_runner(work(reproductions=[("tests/test_passes.py", PASSING)]), repros=[asking])

    result, scripted = run_fix(repo, monkeypatch, fake=fake, before=("--json",))

    assert result.exit_code == 4, result.output
    ref = latest()
    assert ref.manifest.status == "failed" and ref.manifest.verdict == "needs-info"
    state = state_of(ref)
    assert state.needs_info[:2] == ["Which Python version do you use?", "What input did you type?"]
    assert len(state.needs_info) >= 3  # the controller adds the questions every report needs
    assert [s.name for s in ref.manifest.stages if s.status == "failed"] == ["reproduce"]
    assert "fix" not in [r.stage.name for r in scripted.requests]  # nothing was fixed blind
    assert git(repo, "show", "HEAD:app/store.py") == "def add(items, text):\n    return [text]"
    assert (repo / "app" / "store.py").read_text() == BUGGY["app/store.py"]
    assert before["app/store.py"][0] == (repo / "app" / "store.py").read_bytes()
    assert "fix.needs_info" in event_types(ref)
    data = json.loads(result.stdout)
    assert data["verdict"] == "needs-info" and data["exit_code"] == 4
    assert data["questions"][0] == "Which Python version do you use?"
    assert "--allow-unreproduced" in " ".join(data["next_steps"])
    assert "engineering-team resume" not in " ".join(data["next_steps"])
    assert "needs more information" in result.stderr


def test_a_command_that_cannot_start_is_not_a_reproduction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    broken = Repro(
        command="python -m pytest tests/missing_file.py", files=["tests/test_regression.py"]
    )

    result, _ = run_fix(repo, monkeypatch, fake=fix_runner(repros=[broken]))

    assert result.exit_code == 4, (
        result.output
    )  # pytest exit 4: file not found is not a failing test
    notes = " ".join(state_of(latest()).fix.notes)  # type: ignore[union-attr]
    assert "not a failing test" in notes or "no test" in notes.lower() or "usage" in notes


def test_allow_unreproduced_fixes_without_a_failing_test_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    fake = fix_runner(work(reproductions=[("tests/test_passes.py", PASSING)]), repros=[NOT_RED])

    result, _ = run_fix(repo, monkeypatch, "--allow-unreproduced", fake=fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    state = state_of(ref)
    assert state.fix is not None and not state.fix.reproduced and state.fix.allow_unreproduced
    assert state.fix.red is None and state.fix.green is None
    assert state.repro is not None  # the stage counts as finished, so a resume keeps it
    assert all(c.id != "repro" for c in state.checks)
    summary = (ref.run_dir / SUMMARY_FILE).read_text()
    assert "Reproduced: **no.**" in summary and "Nothing here proves the bug is gone" in summary
    assert "fix.unreproduced" in event_types(ref)


# -- what the person gives --------------------------------------------------------------------


def test_the_persons_command_must_fail_first_and_pass_after_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)

    result, fake = run_fix(repo, monkeypatch, "--repro", "python check_bug.py")

    assert result.exit_code == 0, result.output
    ref = latest()
    state = state_of(ref)
    assert state.fix is not None and state.fix.user_repro == "python check_bug.py"
    assert state.fix.user_red is not None and state.fix.user_red.status == "failed"
    by_id = {c.id: c for c in state.checks}
    assert by_id["repro-user"].status == "passed" and by_id["repro"].status == "passed"
    assert "Reproduction command from the user" in (ref.run_dir / "request.md").read_text()
    triage = next(r for r in fake.requests if r.stage.name == "triage")
    assert "python check_bug.py" in CrewStageRunner._inputs(triage)["bug"]
    reproduce = next(r for r in fake.requests if r.stage.name == "reproduce")
    brief = CrewStageRunner._inputs(reproduce)["bug"]  # by now the controller has run it
    assert "python check_bug.py" in brief and "it fails" in brief
    assert (ref.run_dir / "fix-input.json").is_file()


def test_a_stack_trace_is_parsed_and_its_project_files_are_the_suspects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    trace = tmp_path / "crash.log"
    trace.write_text("2026-10-03 INFO boot\n" + TRACE)

    result, fake = run_fix(repo, monkeypatch, "--trace-file", str(trace), report=None)

    assert result.exit_code == 0, result.output  # a trace alone is a report
    ref = latest()
    state = state_of(ref)
    assert state.fix is not None and state.fix.trace is not None
    assert state.fix.trace.error == "IndexError"
    assert state.fix.suspects == ["app/store.py", "app/main.py"]
    request = (ref.run_dir / "request.md").read_text()
    assert "## Stack trace or log" in request and "IndexError: list index out of range" in request
    triage = next(r for r in fake.requests if r.stage.name == "triage")
    brief = CrewStageRunner._inputs(triage)["bug"]
    assert "IndexError: list index out of range" in brief
    assert "Project files in the trace: app/store.py, app/main.py" in brief


# -- resume and refusals ----------------------------------------------------------------------


def test_a_failed_fix_run_resumes_without_reproducing_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    first, _ = run_fix(repo, monkeypatch, fake=fix_runner(fail={"fix": 9}))
    assert first.exit_code == 1, first.output
    ref = latest()
    healthy = use_runner(monkeypatch, fix_runner())

    result = runner.invoke(
        app, ["--workspace-root", str(Path.cwd() / WORKSPACE_ROOT), "resume", ref.run_id]
    )

    assert result.exit_code == 0, result.output
    again = RunStore(repo.resolve()).load(ref.run_id)
    assert again.status == "succeeded" and again.verdict == "verified" and again.resumes == 1
    ran = [r.stage.name for r in healthy.requests if r.stage.kind != "analyze"]
    assert ran[:1] == ["fix"] and "reproduce" not in ran and "triage" not in ran
    record = state_of(ref).fix
    assert record is not None and record.green is not None


def test_usage_errors_exit_2_and_touch_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    before = snapshot(repo)
    use_runner(monkeypatch, fix_runner())

    nothing = runner.invoke(app, ["fix", "--repo", str(repo)])
    no_trace = runner.invoke(
        app, ["fix", "--repo", str(repo), "--trace-file", str(tmp_path / "gone.log")]
    )
    empty = runner.invoke(app, ["fix", "--repo", str(repo), "--request", REPORT, "--repro", " "])
    missing = runner.invoke(app, ["fix", "--repo", str(tmp_path / "nowhere"), "--request", REPORT])

    assert nothing.exit_code == 2 and "Nothing to fix" in nothing.stderr
    assert no_trace.exit_code == 2 and "Cannot read the trace file" in no_trace.stderr
    assert empty.exit_code == 2 and "--repro is empty" in empty.stderr
    assert missing.exit_code == 2 and "is not a directory" in missing.stderr
    assert snapshot(repo) == before and not (repo / ".engineering-team").exists()
    assert git(repo, "branch", "--list", "engineering-team/*") == ""


def test_needs_info_is_exit_4_and_has_its_own_failure_line() -> None:
    verdict: RunVerdict = "needs-info"
    result = RunResult(
        run_id="r1", status="failed", error="The bug was not reproduced.", verdict=verdict
    )

    assert exit_code_for(result) == 4
    line = failure_line(result, resumable=True)
    assert line is not None and "needs more information" in line and "resume" not in line


def test_the_fix_recipe_is_bundled_and_valid() -> None:
    from engineering_team.pipeline.recipes import bundled_recipes, load_recipe

    assert "fix" in bundled_recipes()
    recipe = load_recipe("fix")
    assert [s.name for s in recipe.stages][:3] == ["profile", "baseline", "map"]
    assert recipe.stage("reproduce").kind == "reproduce"
    assert recipe.stage("fix").outputs == ["fix_note"]
