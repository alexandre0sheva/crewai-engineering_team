"""The Verifier: the controller runs each check itself and records what it saw, never what an
agent claimed."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from engineering_team.contracts import CheckKind, CheckSpec
from engineering_team.runtime.cancel import RunCancelled
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import EVENTS_FILENAME
from engineering_team.verification.cards import CheckCards
from engineering_team.verification.revision import verification_revision
from engineering_team.verification.verifier import Verifier

MakeContext = Callable[..., RunContext]

PYPROJECT = '[project]\nname = "calc"\nversion = "0.1.0"\n[tool.pytest.ini_options]\n'
CALC = "def add(a, b):\n    return a + b\n"
TEST_OK = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TEST_BAD = TEST_OK + "\n\ndef test_wrong():\n    assert add(1, 2) == 4, 'one plus two is three'\n"


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checks must find the interpreter (and pytest) that is running these tests."""

    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + ":" + os.environ["PATH"])


def command(
    check_id: str,
    *argv: str,
    kind: CheckKind = "custom",
    required: bool = True,
    timeout: float = 20,
) -> CheckSpec:
    return CheckSpec(
        id=check_id, name=check_id, argv=list(argv), kind=kind, source="user",
        required=required, timeout=timeout,
    )  # fmt: skip


def detected(kind: CheckKind, check_id: str, cwd: str = ".") -> CheckSpec:
    return CheckSpec(id=check_id, name=check_id, kind=kind, source="detected", cwd=cwd)


def run(ctx: RunContext, *checks: CheckSpec, **options: object) -> dict[str, object]:
    results = Verifier(ctx, **options).run(list(checks))  # type: ignore[arg-type]
    return {result.id: result for result in results}


def test_a_passing_command_is_recorded_with_what_the_controller_saw(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("ok.py", "print('all fine')\n")

    (result,) = Verifier(ctx).run([command("ok", "python", "ok.py")])

    assert result.status == "passed" and result.exit_code == 0 and result.required
    assert result.command == "python ok.py" and result.source == "user"
    assert result.started_at is not None and result.duration >= 0
    assert result.revision == verification_revision(ctx.workspace)
    assert result.log_path and (ctx.workspace.root / result.log_path).is_file()
    assert "all fine" in (ctx.workspace.root / result.log_path).read_text(encoding="utf-8")


def test_a_failing_command_keeps_its_exit_code_log_tail_and_suspect_files(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("calc.py", CALC)
    ctx.workspace.write_file(
        "fail.py", "print('calc.py:2: assertion failed')\nraise SystemExit(3)\n"
    )

    (result,) = Verifier(ctx).run([command("fail", "python", "fail.py")])

    assert result.status == "failed" and result.exit_code == 3
    assert "calc.py:2: assertion failed" in result.log_tail
    assert result.suspect_files == ["calc.py"]
    assert result.summary == "exit code 3"


def test_a_missing_program_is_unavailable_never_a_pass(make_context: MakeContext) -> None:
    ctx = make_context()

    (result,) = Verifier(ctx).run([command("tool", "no-such-tool-xyz", "--check")])

    assert result.status == "unavailable" and result.exit_code is None
    assert result.hint and "no-such-tool-xyz" in result.hint


def test_a_command_the_controller_may_not_run_is_unavailable_with_the_reason(
    make_context: MakeContext,
) -> None:
    ctx = make_context()

    (result,) = Verifier(ctx).run([command("inline", "python", "-c", "print(1)")])

    assert result.status == "unavailable" and "Inline code execution is disabled" in (
        result.hint or ""
    )


def test_a_check_that_runs_too_long_fails(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("slow.py", "import time\ntime.sleep(30)\n")

    (result,) = Verifier(ctx).run([command("slow", "python", "slow.py", timeout=1)])

    assert result.status == "failed" and "timed out after 1s" in result.summary


def test_a_smoke_check_passes_while_the_program_keeps_running_and_fails_when_it_crashes(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("serve.py", "import time\nprint('listening')\ntime.sleep(30)\n")
    ctx.workspace.write_file("crash.py", "raise SystemExit('boom')\n")
    ctx.workspace.write_file("once.py", "print('done')\n")

    results = run(
        ctx,
        command("up", "python", "serve.py", kind="smoke", timeout=1),
        command("down", "python", "crash.py", kind="smoke", timeout=5),
        command("once", "python", "once.py", kind="smoke", timeout=5),
    )

    assert (results["up"].status, results["down"].status, results["once"].status) == (  # type: ignore[attr-defined]
        "passed", "failed", "passed",
    )  # fmt: skip
    assert "still running after 1s" in results["up"].summary  # type: ignore[attr-defined]


def test_a_failed_required_setup_skips_the_checks_that_depend_on_it(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("bad.py", "raise SystemExit(1)\n")
    ctx.workspace.write_file("ok.py", "print(1)\n")

    results = run(
        ctx,
        command("setup", "python", "bad.py", kind="setup"),
        command("tests", "python", "ok.py", kind="test"),
    )

    assert results["setup"].status == "failed"  # type: ignore[attr-defined]
    assert results["tests"].status == "skipped"  # type: ignore[attr-defined]
    assert "setup" in (results["tests"].hint or "")  # type: ignore[attr-defined]


def test_a_failed_optional_setup_does_not_stop_the_rest(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("bad.py", "raise SystemExit(1)\n")
    ctx.workspace.write_file("ok.py", "print(1)\n")

    results = run(
        ctx,
        command("setup", "python", "bad.py", kind="setup", required=False),
        command("tests", "python", "ok.py", kind="test"),
    )

    assert results["tests"].status == "passed"  # type: ignore[attr-defined]


# -- detected defaults use the structured developer tools -----------------------------------------


def tiny_project(ctx: RunContext, tests: str) -> None:
    ctx.workspace.write_file("pyproject.toml", PYPROJECT)
    ctx.workspace.write_file("calc.py", CALC)
    ctx.workspace.write_file("tests/test_calc.py", tests)


def test_a_detected_test_check_attaches_the_parsed_report(make_context: MakeContext) -> None:
    ctx = make_context()
    tiny_project(ctx, TEST_BAD)

    (result,) = Verifier(ctx).run([detected("test", "tests")])

    assert result.status == "failed" and result.kind == "test" and result.source == "detected"
    assert result.summary == "1 passed, 1 failed, 0 skipped"
    assert result.report is not None and result.report["framework"] == "pytest"
    assert [f["test_id"] for f in result.report["failures"]] == ["tests/test_calc.py::test_wrong"]
    assert "one plus two is three" in result.log_tail
    assert result.suspect_files == ["tests/test_calc.py"]
    assert "pytest" in result.command and "<report>" in result.command


def test_a_detected_test_check_passes_when_the_suite_passes(make_context: MakeContext) -> None:
    ctx = make_context()
    tiny_project(ctx, TEST_OK)

    (result,) = Verifier(ctx).run([detected("test", "tests")])

    assert result.status == "passed" and result.summary == "1 passed, 0 failed, 0 skipped"
    assert result.log_tail == "" and result.suspect_files == []


def test_a_suite_with_no_tests_is_a_failure_not_a_pass(make_context: MakeContext) -> None:
    ctx = make_context()
    tiny_project(ctx, "# no tests here\n")

    (result,) = Verifier(ctx).run([detected("test", "tests")])

    assert result.status == "failed" and "No tests ran" in (result.hint or result.summary)


def test_a_project_nobody_set_up_has_no_tests_to_pass(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("README.md", "# hello\n")

    (result,) = Verifier(ctx).run([detected("test", "tests")])

    assert result.status == "failed" and "No project detected" in (result.hint or "")


def test_a_missing_test_runner_is_unavailable(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("package.json", '{"devDependencies": {"jest": "^29"}}')
    real_which = shutil.which
    monkeypatch.setattr(
        "engineering_team.execution.backend.shutil.which",
        lambda name, *a, **k: None if name == "npx" else real_which(name, *a, **k),
    )

    (result,) = Verifier(ctx).run([detected("test", "tests")])

    assert result.status == "unavailable" and "Node.js" in (result.hint or "")


# -- browser_script ----------------------------------------------------------------------------


def script_check(path: str) -> CheckSpec:
    return CheckSpec(
        id="e2e", name="e2e", type="browser_script", script=path, source="user", timeout=20
    )


def test_a_browser_script_runs_the_users_script(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("e2e/flow.py", "print('flow ok')\n")
    ctx.workspace.write_file("e2e/bad.py", "raise SystemExit('flow broke')\n")

    results = Verifier(ctx).run([script_check("e2e/flow.py")])
    bad = Verifier(ctx).run([script_check("e2e/bad.py")])

    assert results[0].status == "passed" and results[0].command == "python e2e/flow.py"
    assert bad[0].status == "failed" and "flow broke" in bad[0].log_tail


def test_a_browser_script_that_changed_since_the_run_started_is_not_run(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("e2e/flow.py", "print('flow ok')\n")
    pinned = {"e2e/flow.py": "0" * 64}

    (result,) = Verifier(ctx, script_digests=pinned).run([script_check("e2e/flow.py")])

    assert result.status == "failed" and "changed since the run started" in (result.hint or "")
    assert result.exit_code is None


def test_a_browser_script_without_playwright_is_unavailable(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file(
        "e2e/flow.py", "raise ModuleNotFoundError(\"No module named 'playwright'\")\n"
    )

    (result,) = Verifier(ctx).run([script_check("e2e/flow.py")])

    assert result.status == "unavailable" and "playwright install" in (result.hint or "")


# -- the record ---------------------------------------------------------------------------------


def test_results_events_and_the_round_file_are_recorded(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("ok.py", "print(1)\n")

    Verifier(ctx).run([command("ok", "python", "ok.py")], round=2)

    saved = json.loads((ctx.run_dir / "verification" / "round-2.json").read_text(encoding="utf-8"))
    assert saved["round"] == 2 and saved["revision"] == verification_revision(ctx.workspace)
    assert [r["id"] for r in saved["results"]] == ["ok"]
    events = [e for e in read_events(ctx.run_dir / EVENTS_FILENAME) if e.type.startswith("check.")]
    assert [e.type for e in events] == ["check.started", "check.finished"]
    assert events[1].data["check"] == "ok" and events[1].data["status"] == "passed"


def test_the_controllers_own_report_files_do_not_move_the_revision(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("src.py", "x = 1\n")
    before = verification_revision(ctx.workspace)

    ctx.workspace.write_file("docs/verification.md", "# controller report\n")
    ctx.workspace.write_file("docs/qa-notes.md", "# agent notes\n")
    assert verification_revision(ctx.workspace) == before

    ctx.workspace.write_file("src.py", "x = 2\n")
    assert verification_revision(ctx.workspace) != before


def test_every_check_is_a_board_card_the_controller_moves(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("ok.py", "print(1)\n")
    ctx.workspace.write_file("bad.py", "raise SystemExit(1)\n")
    mapping: dict[str, str] = {}
    cards = CheckCards(ctx, mapping, stage="verify", parent=None)

    Verifier(ctx, cards=cards).run(
        [
            command("ok", "python", "ok.py"),
            command("bad", "python", "bad.py"),
            command("gone", "no-such-tool-xyz"),
        ]
    )

    board = ctx.board
    by_title = {card.title: card for card in board.cards(kind="check")}
    assert {t: c.status for t, c in by_title.items()} == {
        "ok": "done", "bad": "failed", "gone": "blocked",
    }  # fmt: skip
    assert set(mapping) == {"ok", "bad", "gone"}
    done = by_title["ok"]
    assert done.evidence == ["ok"] and done.stage == "verify"
    assert [m.to_status for m in done.history] == ["ready", "in_progress", "verifying", "done"]
    assert {m.actor for card in by_title.values() for m in card.history} == {"controller"}
    assert "exit code 1" in by_title["bad"].history[-1].note


def test_a_check_that_fails_again_after_a_repair_reuses_its_card_and_a_done_one_gets_a_new_card(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    script = ctx.workspace.root / "flaky.py"
    ctx.workspace.write_file("flaky.py", "raise SystemExit(1)\n")
    mapping: dict[str, str] = {}
    verifier = Verifier(ctx, cards=CheckCards(ctx, mapping, stage="verify", parent=None))
    check = command("flaky", "python", "flaky.py")

    verifier.run([check])
    first = mapping["flaky"]
    script.write_text("print('fixed')\n", encoding="utf-8")
    verifier.run([check], round=1)
    assert mapping["flaky"] == first  # the failed card was reopened
    assert ctx.board.get(first).status == "done"

    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    verifier.run([check], round=2)  # a done card is final: the regression gets a new card
    assert mapping["flaky"] != first
    assert ctx.board.get(mapping["flaky"]).status == "failed"
    assert ctx.board.get(first).status == "done"


def test_cancelling_the_run_stops_verification(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.workspace.write_file("ok.py", "print(1)\n")
    ctx.cancel_event.set()

    with pytest.raises(RunCancelled):
        Verifier(ctx).run([command("ok", "python", "ok.py")])
