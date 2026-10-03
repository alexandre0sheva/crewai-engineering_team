"""``new`` and ``resume`` through the real entry point: live view, plain log, JSON, exit codes."""

from __future__ import annotations

import json

import pytest
from cli_helpers import (
    json_out,
    latest_run,
    new_args,
    plain,
    project,
    recording_console,
    run_new,
    use_runner,
    workspace_root,
)
from pipeline_fakes import FakeRunner

from engineering_team import main
from engineering_team.cli.context import Globals
from engineering_team.pipeline.state import RunResult
from engineering_team.runtime.run_store import RunStore


def test_a_run_prints_timestamped_log_lines_and_a_plain_summary_when_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_new(monkeypatch)

    out = capsys.readouterr()
    assert code == 0
    assert "stage spec started" in out.out and "stage release finished" not in out.err
    assert "Run " in out.out and ": verified" in out.out or ": succeeded" in out.out
    assert "Workspace: " in out.out and "Check " in out.out
    assert all(not line.startswith("╭") for line in out.out.splitlines())  # no panel in a pipe
    first = out.out.splitlines()[0]
    assert first[:4].isdigit() and first[4] == "-"  # a timestamp comes first


def test_quiet_keeps_warnings_and_the_result_but_not_every_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_new(monkeypatch, "--quiet")

    out = capsys.readouterr().out
    assert code == 0
    assert "stage spec started" not in out and "Workspace: " in out


def test_json_is_one_parseable_document_and_nothing_else_reaches_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_new(monkeypatch, "--json")

    out = capsys.readouterr()
    data = json_out(out.out)
    assert code == 0 and isinstance(data, dict)
    assert {
        "run_id", "project", "status", "verdict", "exit_code", "error", "duration_seconds",
        "usage", "files_changed", "stages", "checks", "workspace", "report", "next_steps",
    } <= set(data)  # fmt: skip
    assert data["status"] == "succeeded" and data["exit_code"] == 0
    assert [s["name"] for s in data["stages"]][:2] == ["spec", "plan"]
    assert data["checks"][0]["status"] == "passed"
    assert set(data["usage"]) >= {"tokens", "model_calls", "tool_calls", "estimated_cost_usd"}
    assert data["files_changed"]["added"] > 0
    assert "stage spec" not in out.out  # the log lines went to stderr


def test_the_live_view_shows_progress_the_board_and_the_summary_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console, sink = recording_console(140)
    monkeypatch.setattr(Globals, "interactive", property(lambda self: True))
    monkeypatch.setattr(Globals, "console", lambda self, stderr=False: console)
    slow = FakeRunner()
    use_runner(monkeypatch, slow)

    code = main.run(new_args("--provider", "openai"))

    drawn = plain(sink.getvalue())
    assert code == 0
    assert "Run 2" in drawn and "pipeline" in drawn  # the header
    assert "cards" in drawn and "%" in drawn  # progress bar
    assert "Backlog" in drawn and "Done" in drawn  # the kanban columns
    assert "Activity" in drawn and "stage release succeeded" in drawn  # the feed
    assert "Independent checks" in drawn and "Next:" in drawn  # the summary panel


def test_the_legacy_form_runs_new_and_says_it_is_deprecated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch)
    legacy = new_args()[1:]  # the 0.1.0 form: no command word

    code = main.run(legacy)

    err = capsys.readouterr().err
    assert code == 0
    assert "Deprecated: `engineering-team " in err and "--request" in err and "0.3.0" in err
    assert RunStore(project()).latest() is not None


def test_a_help_request_is_not_mistaken_for_the_legacy_form(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.run(["--help"]) == 0

    out = capsys.readouterr()
    assert "Commands" in out.out and "Deprecated" not in out.err


def test_global_options_work_before_or_after_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.run(["--workspace-root", str(workspace_root()), "--json", "runs"]) == 0
    first = capsys.readouterr().out
    assert main.run(["runs", "--json", "--workspace-root", str(workspace_root())]) == 0

    assert json.loads(first) == json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize(
    ("status", "verdict", "code"),
    [
        ("succeeded", "verified", 0),
        ("failed", None, 1),
        ("failed", "failed", 3),
        ("failed", "partial", 4),
        ("cancelled", None, 130),
    ],
)
def test_exit_codes_follow_how_a_run_ended(status: str, verdict: str | None, code: int) -> None:
    result = RunResult(run_id="x", status=status, verdict=verdict)  # type: ignore[arg-type]

    assert main.exit_code_for(result) == code


def test_a_cancelled_run_exits_130_and_names_the_resume_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_new(monkeypatch, runner=FakeRunner(cancel_at="foundation"))

    assert code == 130
    assert f"resume {latest_run()}" in capsys.readouterr().err


def test_a_usage_error_is_exit_2_in_one_line_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main.run(["new", "--request", "x", "--example", "tiny-notes"])

    assert code == 2
    assert "Use only one of" in capsys.readouterr().err


def test_a_json_run_that_fails_before_starting_still_says_so_in_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main.run(["--json", "new", "--workspace-root", str(workspace_root())])

    out = capsys.readouterr().out
    assert code == 2 and json.loads(out) == {"status": "error", "exit_code": 2}


def test_resume_finds_the_run_by_a_prefix_without_a_project_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _ = run_new(monkeypatch, runner=FakeRunner(cancel_at="foundation"))
    assert code == 130
    run_id = latest_run()
    use_runner(monkeypatch)

    resumed = main.run(["resume", run_id[:18], "--workspace-root", str(workspace_root())])

    assert resumed == 0
    assert RunStore(project()).load(run_id).status == "succeeded"


def test_crewai_verbose_output_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    real = main._prepare_from_args

    def spy(args: object, mode: str = "build") -> object:
        prepared = real(args, mode)
        seen.append(prepared.ctx.settings.verbose)  # type: ignore[attr-defined]
        return prepared

    monkeypatch.setattr(main, "_prepare_from_args", spy)
    use_runner(monkeypatch)
    main.run(new_args("--prepare-only"))
    main.run(new_args("--prepare-only", "-v", name="loud"))

    assert seen == [False, True]


def test_replay_names_its_project_by_run_id_instead_of_guessing_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    code, _ = run_new(monkeypatch, name="other-project")
    assert code == 0
    run_id = latest_run("other-project")
    replayed: list[tuple[str, str]] = []

    class FakeTeam:
        def __init__(self, ctx: object) -> None:
            self.ctx = ctx

        def crew(self) -> object:
            name = self.ctx.settings.project_name  # type: ignore[attr-defined]
            return SimpleNamespace(replay=lambda task_id: replayed.append((name, task_id)))

    monkeypatch.setattr(main, "EngineeringTeam", FakeTeam)
    monkeypatch.setattr(
        "sys.argv",
        ["replay", "task-42", "--run", run_id[:14], "--workspace-root", str(workspace_root())],
    )

    assert main.replay() == 0

    assert replayed == [("other-project", "task-42")]


def test_replay_without_a_task_id_or_with_an_unknown_run_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["replay"])
    assert main.replay() == 2
    assert "Usage: replay <task-id>" in capsys.readouterr().err

    monkeypatch.setattr(
        "sys.argv", ["replay", "t1", "--run", "nope", "--workspace-root", str(workspace_root())]
    )
    assert main.replay() == 2
    assert "No runs found" in capsys.readouterr().err
