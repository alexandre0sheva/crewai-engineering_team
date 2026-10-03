"""The false-success matrix: whatever the agents write or claim, a run is ``verified`` only when
the controller ran the checks itself and they passed on the project as it is at the end."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pipeline_fakes import TAIL, FakeRunner, write_checks
from test_pipeline_crews import scripts, write
from test_pipeline_flow import ROOT, only_run, project, resume, run_dir, use_runner

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import CrewStageRunner, StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import RunStore

REQUEST = "Build a tiny notes CLI with add and list commands."
CHECK_PY = (
    "import pathlib, sys\n"
    "if pathlib.Path('flag.txt').is_file():\n"
    "    sys.exit(0)\n"
    "print('check.py:3: flag.txt is missing')\n"
    "sys.exit(1)\n"
)
# One required test check that passes only while flag.txt exists; it proves AC-1.
CHECKS = (
    "- {id: tests, name: Project tests, kind: test, command: python check.py, criteria: [AC-1]}\n"
)
Hook = Callable[[StageRequest], None]


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + ":" + os.environ["PATH"])


def launch(checks: str | None = CHECKS, *extra: str) -> int:
    argv = [
        "--request", REQUEST, "--project-name", "demo",
        "--workspace-root", str(Path.cwd() / ROOT), "--strategy", "pipeline",
    ]  # fmt: skip
    if checks is not None:
        argv += ["--checks", str(write_checks(checks))]
    return main.run([*argv, *extra])


def seed(*, flag: bool = True, also: Hook | None = None) -> Hook:
    """A hook that gives the project the check script (and the flag it looks for) at foundation."""

    def hook(request: StageRequest) -> None:
        if request.stage.name == "foundation":
            write = request.ctx.workspace.write_file
            write("check.py", CHECK_PY)
            if flag:
                write("flag.txt", "ok\n")
        if also is not None:
            also(request)

    return hook


def report() -> str:
    return (project() / "docs" / "verification.md").read_text(encoding="utf-8")


def state() -> PipelineState:
    loaded = PipelineState.load(run_dir(only_run()))
    assert loaded is not None
    return loaded


def repairs(runner: FakeRunner) -> list[StageRequest]:
    return [r for r in runner.requests if r.stage.name == "verify"]


# -- the clean path ----------------------------------------------------------------------------


def test_a_run_whose_checks_pass_is_verified_with_a_report_the_controller_wrote(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(on_call=seed()))

    assert launch() == 0

    manifest = only_run()
    assert manifest.status == "succeeded" and manifest.verdict == "verified"
    assert repairs(runner) == []  # no failure, so no agent was asked anything
    text = report()
    assert "**Verdict: VERIFIED**" in text and "## Independent checks" in text
    assert "| tests | test | yes | PASSED | 0 |" in text
    record = state().verification
    assert record.verdict == "verified" and record.rounds == 0
    assert [(c.id, c.status) for c in state().checks] == [("tests", "passed")]
    # The result is stored with the revision it ran against and a log the controller kept.
    saved = json.loads((run_dir(manifest) / "verification" / "round-0.json").read_text("utf-8"))
    assert saved["results"][0]["exit_code"] == 0 and saved["results"][0]["revision"]
    assert "Workspace: " in capsys.readouterr().out


# -- false successes ---------------------------------------------------------------------------


def test_an_agent_that_claims_success_and_writes_a_glowing_report_cannot_make_a_failing_run_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boast(request: StageRequest) -> None:
        if request.stage.name == "integrate":
            write = request.ctx.workspace.write_file
            write("docs/verification.md", "# Verification\nAll 42 tests PASSED. Verified.\n" * 3)
            write("docs/qa-notes.md", "# QA\nEverything passes, ship it.\n")

    use_runner(
        monkeypatch,
        FakeRunner(on_call=seed(flag=False, also=boast), says={"verify": "All tests pass now."}),
    )

    assert launch() == 3

    manifest = only_run()
    assert manifest.status == "failed" and manifest.verdict == "failed"
    text = report()
    assert "**Verdict: FAILED**" in text and "42 tests" not in text and "Verified." not in text
    assert "check.py:3: flag.txt is missing" in text  # what the controller itself saw
    assert "not verified" in capsys.readouterr().err


def test_a_stale_report_from_an_earlier_run_does_not_carry_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(on_call=seed()))
    assert launch() == 0
    first = only_run()
    assert "VERIFIED" in report()

    def break_it(request: StageRequest) -> None:
        if request.stage.name == "integrate":
            (request.ctx.workspace.root / "flag.txt").unlink()

    use_runner(monkeypatch, FakeRunner(on_call=break_it))
    assert launch() == 3  # a new run in the same project: nothing is reused from the old one

    second = next(m for m in RunStore(project()).list_runs() if m.run_id != first.run_id)
    assert second.verdict == "failed"
    assert "**Verdict: FAILED**" in report()


def test_without_any_test_the_project_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())

    assert launch(checks=None) == 3  # the fake project has no tests at all

    assert only_run().verdict == "failed"
    assert "No script named 'test'" in report()  # nothing to run: that is a failure, not a pass


def test_an_altered_check_file_is_caught_and_the_original_cannot_weaken_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def tamper(request: StageRequest) -> None:
        if request.stage.name == "integrate":
            # Both the user's file and the run's pinned copy are replaced with a check that
            # always passes (an agent with a shell could reach the pinned copy).
            weak = "- {id: tests, kind: test, command: 'true'}\n"
            (Path.cwd() / "checks.yaml").write_text(weak, encoding="utf-8")
            (request.ctx.run_dir / "checks.yaml").write_text(weak, encoding="utf-8")

    use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False, also=tamper)))

    assert launch() == 3

    assert only_run().verdict == "failed"
    assert "changed since the run started" in report()


def test_the_users_file_changing_after_the_start_has_no_effect_on_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def weaken_original(request: StageRequest) -> None:
        if request.stage.name == "integrate":
            (Path.cwd() / "checks.yaml").write_text("- {id: tests, command: 'true'}\n", "utf-8")

    use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False, also=weaken_original)))

    assert launch() == 3  # the run kept the check it started with, and it fails


def test_an_edit_after_verification_that_breaks_the_project_is_caught_by_the_final_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sabotage_in_release(request: StageRequest) -> None:
        if request.stage.name == "release":
            (request.ctx.workspace.root / "flag.txt").unlink()  # after verify said "verified"

    use_runner(monkeypatch, FakeRunner(on_call=seed(also=sabotage_in_release)))

    assert launch() == 3

    manifest = only_run()
    assert manifest.verdict == "failed" and "Final verification" in state().error
    assert "**Verdict: FAILED**" in report()
    stage_status = {s.name: s.status for s in manifest.stages}
    assert stage_status["verify"] == "succeeded" and stage_status["release"] == "succeeded"


def test_a_harmless_edit_after_verification_runs_the_checks_again_and_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def touch_in_release(request: StageRequest) -> None:
        if request.stage.name == "release":
            request.ctx.workspace.write_file("docs/extra.md", "# extra\n")

    use_runner(monkeypatch, FakeRunner(on_call=seed(also=touch_in_release)))

    assert launch() == 0

    started = [
        e for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == "check.started"
    ]
    assert len(started) == 2  # once in the verify stage, once more because release edited a file
    assert only_run().verdict == "verified"


def test_nothing_changed_after_verification_means_no_second_run_of_the_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The optional DevOps and docs stages edit the project after verifying, which the final
    # re-verify then (rightly) runs again; this is about a run where nothing edits it.
    monkeypatch.setenv("ENGINEERING_TEAM_PROFILE", "minimal")
    use_runner(monkeypatch, FakeRunner(on_call=seed()))

    assert launch() == 0

    types = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert types.count("check.started") == 1 and "verify.reused" in types


def test_an_agent_overwriting_the_report_after_verification_is_undone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forge(request: StageRequest) -> None:
        if request.stage.name == "release":
            request.ctx.workspace.write_file("docs/verification.md", "# Verification\nPASSED\n")

    use_runner(monkeypatch, FakeRunner(on_call=seed(also=forge)))

    assert launch() == 0

    assert "forge" not in report() and "## Independent checks" in report()
    assert report() != "# Verification\nPASSED\n"


def test_a_required_check_that_cannot_run_makes_the_result_partial_not_verified(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(on_call=seed()))
    checks = CHECKS + "- {id: e2e, name: E2E, command: no-such-tool-xyz}\n"

    assert launch(checks) == 4

    manifest = only_run()
    assert manifest.verdict == "partial" and manifest.status == "failed"
    assert repairs(runner) == []  # nothing an agent can fix by editing code
    text = report()
    assert "**Verdict: PARTIAL**" in text and "UNAVAILABLE" in text
    assert "no-such-tool-xyz" in text
    assert "not verified" in capsys.readouterr().err


def test_a_criterion_no_check_proves_is_listed_for_a_human_not_silently_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(on_call=seed()))

    assert launch() == 0  # AC-1 is proven by the mapped check; AC-2 is not

    text = report()
    assert "| AC-1 | add stores a note | verified | tests |" in text
    manual = text.split("### Manual / unverified")[1]
    assert "AC-2" in manual and "AC-1" not in manual
    assert {c.id: c.status for c in state().verification.coverage} == {
        "AC-1": "verified",
        "AC-2": "unverified",
    }


# -- the repair loop ---------------------------------------------------------------------------


def test_a_failing_check_is_repaired_by_the_agent_and_then_verified_by_the_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fix(request: StageRequest) -> None:
        if request.stage.name == "verify":
            request.ctx.workspace.write_file("flag.txt", "fixed\n")

    monkeypatch.setenv("ENGINEERING_TEAM_PROFILE", "minimal")  # no later stage edits the project
    runner = use_runner(
        monkeypatch,
        FakeRunner(on_call=seed(flag=False, also=fix), says={"verify": "Created flag.txt."}),
    )

    assert launch() == 0

    manifest = only_run()
    assert manifest.verdict == "verified" and manifest.status == "succeeded"
    (request,) = repairs(runner)
    assert request.teammate == "debugger"
    # The agent is handed structured failures: the check, its command, what it printed.
    assert "check `tests`" in request.failures and "python check.py" in request.failures
    assert "check.py:3: flag.txt is missing" in request.failures
    assert "Repair round 1 of 3" in request.failures
    record = state().verification
    assert record.rounds == 1 and "Round 1" in record.repair_log[0]
    assert "Repair rounds used: 1 of 3" in report()
    # What the agent said is kept for people, labelled, outside the controller's report.
    notes = (project() / "docs" / "qa-notes.md").read_text(encoding="utf-8")
    assert "Created flag.txt." in notes and "Created flag.txt." not in report()
    board = BoardStore(run_dir(manifest))
    (check,) = [c for c in board.cards(kind="check")]
    assert check.status == "done" and check.evidence == ["tests"]
    (repair,) = board.cards(kind="repair")
    assert repair.status == "done" and repair.assignee == "debugger"
    assert repair.history[-1].actor == "controller"


def test_a_repair_that_changes_nothing_is_not_believed_and_the_rounds_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(
        monkeypatch,
        FakeRunner(on_call=seed(flag=False), says={"verify": "Fixed everything. All tests pass."}),
    )

    assert launch() == 3

    assert len(repairs(runner)) == 3  # budget.max_repair_rounds
    manifest = only_run()
    assert manifest.verdict == "failed"
    record = state().verification
    assert record.rounds == 3 and all("changed nothing" in entry for entry in record.repair_log)
    assert "Fixed everything" not in report()
    repair_cards = BoardStore(run_dir(manifest)).cards(kind="repair")
    assert [c.status for c in repair_cards] == ["failed"] * 3


def test_the_number_of_repair_rounds_comes_from_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS", "1")
    runner = use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False)))

    assert launch() == 3
    assert len(repairs(runner)) == 1

    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS", "0")
    runner = use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False)))
    assert launch() == 3
    assert repairs(runner) == []


def test_a_repair_that_converges_in_the_second_round_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rounds: list[int] = []

    def fix_on_second(request: StageRequest) -> None:
        if request.stage.name == "verify":
            rounds.append(1)
            request.ctx.workspace.write_file("notes.txt", f"attempt {len(rounds)}\n")
            if len(rounds) == 2:
                request.ctx.workspace.write_file("flag.txt", "ok\n")

    use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False, also=fix_on_second)))

    assert launch() == 0

    record = state().verification
    assert record.rounds == 2 and record.verdict == "verified"
    assert "still failing: tests" in record.repair_log[0]
    assert "all of them pass now" in record.repair_log[1]


def test_a_crashing_repair_agent_uses_up_a_round_but_the_checks_still_decide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False), fail={"verify": 99}))

    assert launch() == 3

    assert len(repairs(runner)) == 3
    assert "The repair agent failed to run" in state().verification.repair_log[0]
    assert "scripted failure" in (project() / "docs" / "qa-notes.md").read_text("utf-8")


def test_a_run_that_failed_verification_resumes_with_fresh_repair_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False)))
    assert launch() == 3
    run_id = only_run().run_id
    assert state().verification.rounds == 3

    def fix(request: StageRequest) -> None:
        if request.stage.name == "verify":
            request.ctx.workspace.write_file("flag.txt", "fixed by hand\n")

    runner = use_runner(monkeypatch, FakeRunner(on_call=fix))
    assert resume(run_id) == 0

    manifest = only_run()
    assert (
        manifest.status == "succeeded" and manifest.verdict == "verified" and manifest.resumes == 1
    )
    assert [call[0] for call in runner.calls] == ["verify", *[s for s, _ in TAIL]]  # reused earlier
    assert state().verification.rounds == 1


def test_cancelling_during_verification_ends_the_run_cancelled_and_it_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engineering_team.verification import verifier as verifier_module

    real = verifier_module.Verifier.run
    armed = [True]

    def run_then_cancel(self, checks, *, round=0):  # type: ignore[no-untyped-def]
        if armed[0]:
            armed[0] = False
            self.ctx.cancel_event.set()
        return real(self, checks, round=round)

    monkeypatch.setattr(verifier_module.Verifier, "run", run_then_cancel)
    use_runner(monkeypatch, FakeRunner(on_call=seed()))

    assert launch() == 130
    assert only_run().status == "cancelled" and only_run().verdict is None

    use_runner(monkeypatch, FakeRunner())
    assert resume(only_run().run_id) == 0
    assert only_run().verdict == "verified"


def test_resuming_with_a_different_checks_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner(on_call=seed(flag=False)))
    assert launch() == 3
    other = write_checks("- {id: tests, command: 'true'}\n", name="other.yaml")

    code = resume(only_run().run_id, "--checks", str(other))

    assert code == 2 and "not the checks file this run started with" in capsys.readouterr().err


def test_a_checks_file_inside_the_project_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner(on_call=seed()))
    assert launch() == 0  # the first run creates the project
    inside = project() / "checks.yaml"
    inside.write_text(CHECKS, encoding="utf-8")

    code = main.run(
        ["--request", REQUEST, "--project-name", "demo", "--workspace-root", str(Path.cwd() / ROOT),
         "--strategy", "pipeline", "--checks", str(inside)]
    )  # fmt: skip

    assert code == 2 and "outside the project" in capsys.readouterr().err


def test_a_checks_file_needs_the_pipeline_strategy(capsys: pytest.CaptureFixture[str]) -> None:
    code = main.run(
        ["--request", REQUEST, "--project-name", "demo", "--workspace-root", str(Path.cwd() / ROOT),
         "--strategy", "single", "--checks", str(write_checks())]
    )  # fmt: skip

    assert code == 2 and "only run by the 'pipeline' strategy" in capsys.readouterr().err


def test_the_real_repair_crew_is_briefed_with_the_failures_and_its_tokens_are_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verify stage's agent is an ordinary CrewAI crew (scripted model, real tools)."""

    models = scripts()
    # The foundation stage leaves a project whose check fails; the debugger repairs it.
    models["backend_engineer"]._script.appendleft(write("check.py", CHECK_PY))
    models["debugger"]._script.extendleft(
        reversed([write("flag.txt", "ok\n"), "Created flag.txt, which check.py needs."])
    )
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: models[key])
    )

    assert launch() == 0

    brief = models["debugger"].calls[0]
    assert "Repair round 1 of 3" in brief.prompt and "check `tests`" in brief.prompt
    assert "check.py:3: flag.txt is missing" in brief.prompt
    assert "Never weaken, delete, skip" in brief.prompt
    assert brief.tools is not None
    assert "write_project_file" in {tool["function"]["name"] for tool in brief.tools}
    usage = json.loads((run_dir(only_run()) / "usage.json").read_text(encoding="utf-8"))
    assert usage["by_stage"]["verify"]["calls"] == 2  # the repair agent's two turns
    notes = (project() / "docs" / "qa-notes.md").read_text(encoding="utf-8")
    assert "Created flag.txt, which check.py needs." in notes
    for llm in models.values():
        llm.assert_exhausted()
