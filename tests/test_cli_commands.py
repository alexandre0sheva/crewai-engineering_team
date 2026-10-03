"""Every information and control command, through Typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_helpers import latest_run, project, run_new, use_runner, workspace_root
from pipeline_fakes import FakeRunner
from typer.testing import CliRunner

from engineering_team import main
from engineering_team.cli.app import app
from engineering_team.cli.init_command import CONFIG_NAME, REQUEST_NAME
from engineering_team.runtime.inbox import pending_commands
from engineering_team.runtime.run_store import RunStore

runner = CliRunner()


def invoke(*args: str, root: bool = True):  # noqa: ANN201
    prefix = ["--workspace-root", str(workspace_root())] if root else []
    return runner.invoke(app, [*prefix, *args])


@pytest.fixture
def finished(monkeypatch: pytest.MonkeyPatch) -> str:
    code, _ = run_new(monkeypatch)
    assert code == 0
    return latest_run()


# -- status, runs, board ------------------------------------------------------------------------


def test_status_shows_the_stages_and_the_workspace(finished: str) -> None:
    result = invoke("status")

    assert result.exit_code == 0
    for word in ("spec", "plan", "release", "succeeded", "Workspace"):
        assert word in result.stdout
    assert finished in result.stdout


def test_status_json_has_a_stable_schema(finished: str) -> None:
    result = invoke("--json", "status", finished[:12])

    data = json.loads(result.stdout)
    assert {
        "run_id", "status", "stage", "paused", "progress", "cards", "usage", "elapsed_seconds",
        "stages", "project", "strategy", "mode", "verdict", "workspace",
    } <= set(data)  # fmt: skip
    assert data["run_id"] == finished and data["status"] == "succeeded"
    assert data["progress"]["overall_percent"] == 100.0
    assert {"id", "title", "status"} <= set(data["cards"][0])
    assert data["verdict"] == "verified"


def test_runs_lists_every_project_and_filters_by_one(
    monkeypatch: pytest.MonkeyPatch, finished: str
) -> None:
    run_new(monkeypatch, name="second")

    everything = json.loads(invoke("--json", "runs").stdout)
    only = json.loads(invoke("--json", "runs", "--project-name", "second").stdout)
    limited = json.loads(invoke("--json", "runs", "--limit", "1").stdout)

    assert {row["project"] for row in everything} == {"demo", "second"}
    assert [row["project"] for row in only] == ["second"]
    assert len(limited) == 1 and limited[0]["project"] == "second"  # the newest
    assert set(everything[0]) >= {"run_id", "status", "verdict", "created", "estimated_cost_usd"}


def test_runs_text_names_each_run_and_how_it_ended(finished: str) -> None:
    result = invoke("runs")

    assert finished in result.stdout and "succeeded" in result.stdout


def test_runs_with_none_says_how_to_start(finished: str) -> None:
    result = runner.invoke(app, ["--workspace-root", str(Path.cwd() / "elsewhere"), "runs"])

    assert result.exit_code == 0 and "engineering-team new" in result.stdout


def test_board_prints_the_kanban_and_json_prints_the_cards(finished: str) -> None:
    text = invoke("board")
    data = json.loads(invoke("--json", "board", finished).stdout)

    assert text.exit_code == 0 and "Done (" in text.stdout  # 80 columns: the compact list
    assert all(card["status"] == "done" for card in data["cards"])
    assert len(data["cards"]) >= 7


def test_board_watch_without_a_terminal_prints_once_and_returns(finished: str) -> None:
    result = invoke("board", "--watch")

    assert result.exit_code == 0 and "Done (" in result.stdout


def test_a_run_that_does_not_exist_is_a_one_line_usage_error(finished: str) -> None:
    result = invoke("status", "20990101")

    assert result.exit_code == 2
    assert "No run '20990101'" in result.stderr and result.stderr.count("\n") == 1


def test_an_ambiguous_prefix_asks_for_more_of_the_id(
    monkeypatch: pytest.MonkeyPatch, finished: str
) -> None:
    run_new(monkeypatch, name="second")

    result = invoke("status", finished[:4])  # both runs started in the same year, month, day

    assert result.exit_code == 2 and "matches several runs" in result.stderr


# -- cancel, note, pause --------------------------------------------------------------------------


def test_cancel_says_a_finished_run_has_nothing_to_cancel(finished: str) -> None:
    result = invoke("cancel", finished)

    assert result.exit_code == 0 and "already succeeded" in result.stdout


def test_a_note_for_a_run_nobody_works_on_waits_until_it_is_resumed(finished: str) -> None:
    result = invoke("note", finished, "Prefer sqlite over files.")

    assert result.exit_code == 0 and "when you resume it" in result.stdout
    [queued] = pending_commands(RunStore(project()).run_dir(finished))
    assert json.loads(queued.read_text()) == {"kind": "note", "text": "Prefer sqlite over files."}


def test_a_note_can_target_a_card_and_the_card_must_exist(finished: str) -> None:
    ok = invoke("note", finished, "Use the new API.", "--card", "K-002")
    bad = invoke("note", finished, "Hello", "--card", "K-999")

    assert ok.exit_code == 0
    assert bad.exit_code == 2 and "No card 'K-999'" in bad.stderr and "K-001" in bad.stderr
    queued = [
        json.loads(p.read_text()) for p in pending_commands(RunStore(project()).run_dir(finished))
    ]
    assert queued == [{"kind": "note", "text": "Use the new API.", "card": "K-002"}]


def test_an_empty_note_is_refused(finished: str) -> None:
    result = invoke("note", finished, "   ")

    assert result.exit_code == 2 and "needs some text" in result.stderr


def test_pause_and_unpause_need_a_running_run(finished: str) -> None:
    for command in ("pause", "unpause"):
        result = invoke(command, finished)
        assert result.exit_code == 2 and "is not running" in result.stderr
    assert pending_commands(RunStore(project()).run_dir(finished)) == []


# -- config, examples, team, init -----------------------------------------------------------------


def test_config_show_prints_every_setting_with_its_source() -> None:
    text = invoke("config", "show", root=False)
    data = json.loads(
        invoke("--json", "config", "show", "--provider", "anthropic", root=False).stdout
    )

    assert text.exit_code == 0 and "execution.backend" in text.stdout and "[default]" in text.stdout
    assert {row["key"]: row["value"] for row in data}["provider"] == "anthropic"
    assert set(data[0]) == {"key", "value", "source"}


def test_an_unknown_provider_is_a_usage_error() -> None:
    result = invoke("config", "show", "--provider", "nonsense", root=False)

    assert result.exit_code == 2


def test_examples_list_and_run_a_bundled_example() -> None:
    listing = json.loads(invoke("--json", "examples", "list", root=False).stdout)
    ran = invoke("examples", "run", "tiny-notes", "--prepare-only", "--project-name", "ex")

    assert [item["name"] for item in listing] == ["tiny-notes"] and listing[0]["title"]
    assert ran.exit_code == 0 and "Prepared project workspace" in ran.stdout
    assert (project("ex") / ".engineering-team" / "owner.json").exists()


def test_team_lists_the_teammates() -> None:
    members = json.loads(invoke("--json", "team", root=False).stdout)
    text = invoke("team", root=False)

    assert {m["key"] for m in members} >= {"engineering_lead", "solution_architect"}
    assert "product_analyst" in text.stdout and "built-in" in text.stdout


def test_init_writes_a_valid_config_and_a_request_template_that_cannot_be_run_unedited() -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--provider",
            "anthropic",
            "--project-name",
            "My App",
            "--sandbox",
            "docker",
        ],
    )

    assert result.exit_code == 0
    from engineering_team.settings import load_settings

    settings = load_settings(config_file=CONFIG_NAME)
    assert settings.provider == "anthropic" and settings.execution.backend == "docker"
    assert settings.project_name == "My App"
    # The template carries the marker, so running it unedited is refused with a clear message.
    assert main.run(["new", "--request-file", REQUEST_NAME, "--prepare-only"]) == 2


def test_init_does_not_overwrite_without_force() -> None:
    assert runner.invoke(app, ["init", "--yes"]).exit_code == 0
    Path(CONFIG_NAME).write_text("# mine\n", encoding="utf-8")

    again = runner.invoke(app, ["init", "--yes"])
    forced = runner.invoke(app, ["init", "--yes", "--force"])

    assert again.exit_code == 2 and "already exist" in again.stderr
    assert Path(CONFIG_NAME).read_text() != "# mine\n" and forced.exit_code == 0


def test_init_prompts_when_run_in_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team.cli.context import Globals

    monkeypatch.setattr(Globals, "interactive", property(lambda self: True))

    result = runner.invoke(app, ["init"], input="google\nsmoke\nhierarchical\nlocal\nDemo\n")

    assert result.exit_code == 0
    text = Path(CONFIG_NAME).read_text()
    assert (
        'provider = "google"' in text
        and 'profile = "smoke"' in text
        and 'project_name = "Demo"' in text
    )


def test_the_version_flag_prints_the_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0 and result.stdout.startswith("engineering-team 0.")


def test_a_cancelled_run_is_resumable_from_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _ = run_new(monkeypatch, runner=FakeRunner(cancel_at="foundation"))
    assert code == 130
    use_runner(monkeypatch)

    result = invoke("resume", latest_run())

    assert result.exit_code == 0
