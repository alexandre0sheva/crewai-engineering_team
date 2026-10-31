"""``engineering-team do``: one entry point; triage, preview, confirmation, and the presets."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from pipeline_fakes import REQUEST, write_checks
from repo_fixtures import make_repo
from test_feature_mode import LEGACY, feature_runner
from test_intake_triage import answer

from engineering_team import main
from engineering_team.cli import app as cli_app
from engineering_team.cli import do_command, do_plan
from engineering_team.cli.app import request_at
from engineering_team.contracts import TaskProfile
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.runtime.run_index import find_runs
from engineering_team.testing.fake_llm import ScriptedLLM, Turn

FEATURE = "Add a search function that finds notes by text."
BUG = "The add function in app/store.py drops the first note."


def triage_says(monkeypatch: pytest.MonkeyPatch, **fields: Any) -> ScriptedLLM:
    """Make the triage call answer ``fields`` (an ``answer()`` of the triage tests)."""

    llm = ScriptedLLM([Turn(json.dumps(answer(**fields).model_dump()), 400, 80)] * 4)
    monkeypatch.setattr(do_plan, "build_triage_llm", lambda settings: llm)
    return llm


def no_triage_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(settings: Any) -> Any:
        raise RuntimeError("no model in this test")

    monkeypatch.setattr(do_plan, "build_triage_llm", refuse)


def do(*args: str, workspace: bool = True) -> int:
    root = ["--workspace-root", str(workspace_root())] if workspace else []
    return main.run([*root, "do", *args])


def plan_of(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "planned"
    plan: dict[str, Any] = document["plan"]
    return plan


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "legacy", LEGACY)


# -- the request ---------------------------------------------------------------------------------


def test_the_request_comes_from_an_argument_an_option_a_file_or_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)
    Path("idea.md").write_text(REQUEST, encoding="utf-8")
    seen = []

    for args, stdin in (
        ([REQUEST], None),
        (["--request", REQUEST], None),
        (["--request-file", "idea.md"], None),
        (["-"], REQUEST + "\n"),
        (["--request-file", "-"], REQUEST + "\n"),
    ):
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin or ""))
        assert do(*args, "--dry-run", "--json") == 0, args
        seen.append(plan_of(capsys)["request"].strip())

    assert seen == [REQUEST] * 5


def test_an_argument_and_the_request_option_together_are_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    assert do(REQUEST, "--request", "something else", "--dry-run") == 2

    assert "only one of the REQUEST argument and --request" in capsys.readouterr().err


def test_a_request_with_no_words_is_the_usual_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert do("--dry-run") == 2

    assert "request" in capsys.readouterr().err.lower()


def test_a_sentence_without_a_command_is_a_request_and_one_word_is_not(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    assert (
        main.run(["--workspace-root", str(workspace_root()), REQUEST, "--dry-run", "--json"]) == 0
    )
    assert plan_of(capsys)["request"] == REQUEST

    assert main.run(["statu"]) == 2  # a typo of a command must not start a paid run
    assert "needs a command or a request" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "at"),
    [
        (["fix the login bug"], 0),
        (["--json", "fix the login bug", "--yes"], 1),
        (["--workspace-root", "ws", "fix the login bug"], 2),
        (["statu"], None),
        (["--request", "fix the login bug"], None),
        (["--help"], None),
        ([], None),
    ],
)
def test_where_a_bare_request_starts(argv: list[str], at: int | None) -> None:
    assert request_at(argv) == at


def test_a_bundled_example_runs_through_do(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])

    assert do("--example", "tiny-notes", "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["route"] == "new" and "notes" in plan["request"].lower()
    assert do("--example", "tiny-notes", "--request", "also this", "--dry-run") == 2


# -- the project ---------------------------------------------------------------------------------


@pytest.mark.git
def test_the_current_directory_is_the_project_when_it_is_a_git_repository(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    monkeypatch.chdir(legacy)

    assert do(FEATURE, "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["project"] == str(legacy.resolve()) and plan["route"] == "repository"
    assert plan["profile"]["target"]["kind"] == "existing"


def test_without_a_repository_the_request_is_a_new_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])

    assert do(REQUEST, "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["project"] is None and plan["route"] == "new"
    assert plan["recipe"] == "composed-new-project"


@pytest.mark.git
def test_repo_names_the_project_wherever_you_are(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["bugfix"])

    assert do(BUG, "--repo", str(legacy), "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["project"] == str(legacy.resolve())
    assert plan["profile"]["intents"][0] == "bugfix" and plan["recipe"] == "composed-bugfix"


def test_repo_without_project_files_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])
    empty = tmp_path / "empty"
    empty.mkdir()

    assert do(REQUEST, "--repo", str(empty), "--dry-run") == 2

    assert "has no project files" in capsys.readouterr().err
    assert do(REQUEST, "--repo", str(tmp_path / "missing"), "--dry-run") == 2


# -- the preview ---------------------------------------------------------------------------------


@pytest.mark.git
def test_the_preview_shows_the_triage_the_stages_with_reasons_the_rung_and_the_cost(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature", "docs"], size="medium", rationale="Adds search.")

    assert do(FEATURE, "--repo", str(legacy), "--dry-run") == 0

    out = capsys.readouterr().out
    assert "Understood as: feature, docs on the project at" in out
    assert "size medium, risk low, confidence high (model)" in out and "Adds search." in out
    assert "Plan: strategy auto, starting on the" in out and "rung" in out
    assert " implement" in out and "Why:" not in out  # the reason sits under each stage
    assert "The request also asks for documentation." in out
    assert "Estimated cost: about $" in out and "no cost limit set" in out
    assert "Triage: " in out and "480 tokens" in out


@pytest.mark.git
def test_a_dry_run_changes_nothing_and_calls_no_team(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    fake = use_runner(monkeypatch, feature_runner())
    before = sorted(p.name for p in legacy.iterdir())

    assert do(FEATURE, "--repo", str(legacy), "--dry-run") == 0

    assert sorted(p.name for p in legacy.iterdir()) == before
    assert not workspace_root().exists() and not fake.calls


def test_a_preview_needs_no_credentials_and_falls_back_to_the_signals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert do(REQUEST, "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["profile"]["source"] == "signals" and plan["triage_usage"] is None
    assert any("no model call for triage" in note for note in plan["notes"])
    assert do(REQUEST, "--yes") == 2  # a run without credentials is refused before any call


def test_the_plan_is_the_json_a_script_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"], size="large")
    monkeypatch.setenv("ENGINEERING_LEAN_SIZE", "large")  # how the controller sizes the run

    assert do(REQUEST, "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["profile"]["size"] == "large" and plan["size"] == "large"
    assert plan["ladder"]["start"] == "pipeline" and plan["ladder"]["max"] == "pipeline"
    assert [s["name"] for s in plan["stages"]][:2] == ["design", "spec"]
    assert all(s["why"] for s in plan["stages"])
    assert plan["estimate_usd"]["first"] is not None and plan["triage_usage"]["model"]


# -- forcing the intent --------------------------------------------------------------------------


@pytest.mark.git
@pytest.mark.parametrize(
    ("mode", "recipe"),
    [("fix", "fix"), ("feature", "feature"), ("add-tests", "add-tests"), ("docs", "docs")],
)
def test_mode_forces_the_intent_and_composes_the_recipe_of_the_preset(
    mode: str,
    recipe: str,
    legacy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The model is overruled; a medium task is the canonical profile the bundled recipes match.
    triage_says(monkeypatch, intents=["new_project"], size="medium")

    assert do("Do the work.", "--repo", str(legacy), "--mode", mode, "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    names = [s["name"] for s in plan["stages"]]
    assert names == [s.name for s in load_recipe(recipe, legacy).stages]
    assert any("--mode" in rule for rule in plan["profile"]["overrides"])


def test_a_mode_that_needs_a_project_refuses_to_run_without_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch)

    assert do(BUG, "--mode", "fix", "--dry-run") == 2

    assert "--mode fix works on an existing project" in capsys.readouterr().err


@pytest.mark.git
def test_mode_new_refuses_a_project_that_has_code(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch)

    assert do(REQUEST, "--repo", str(legacy), "--mode", "new", "--dry-run") == 2

    assert "--mode new makes a new project" in capsys.readouterr().err


def test_an_unknown_mode_and_an_unknown_recipe_are_usage_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])

    assert do(REQUEST, "--mode", "dance", "--dry-run") == 2
    assert do(REQUEST, "--recipe", "no-such-recipe", "--dry-run") == 2
    assert "no-such-recipe" in capsys.readouterr().err


# -- asking to proceed ---------------------------------------------------------------------------


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("yes", "interactive", "tty", "answer_", "starts"),
    [
        (True, None, True, False, True),  # --yes never asks
        (False, False, True, False, True),  # --non-interactive never asks
        (False, None, False, False, True),  # no terminal: nobody to ask
        (False, None, True, True, True),  # asked, and the answer is yes
        (False, None, True, False, False),  # asked, and the answer is no
    ],
)
def test_the_person_is_asked_only_when_someone_can_answer(
    yes: bool,
    interactive: bool | None,
    tty: bool,
    answer_: bool,
    starts: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engineering_team.cli.context import Globals

    monkeypatch.setattr(sys, "stdin", Terminal() if tty else io.StringIO())
    monkeypatch.setattr(sys, "stderr", Terminal() if tty else io.StringIO())
    monkeypatch.setattr(do_command.typer, "confirm", lambda *a, **k: answer_)

    assert (
        do_command._proceed(Globals(), yes=yes, interactive=interactive, stdin_used=False) is starts
    )


def test_json_and_stdin_never_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team.cli.context import Globals

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())
    monkeypatch.setattr(do_command.typer, "confirm", lambda *a, **k: False)

    assert do_command._proceed(Globals(json=True), yes=False, interactive=None, stdin_used=False)
    assert do_command._proceed(Globals(), yes=False, interactive=None, stdin_used=True)


def test_declining_the_preview_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])
    fake = use_runner(monkeypatch)
    monkeypatch.setattr(do_command, "_proceed", lambda *a, **k: False)

    assert do(REQUEST) == 1

    assert "Not started: nothing was changed." in capsys.readouterr().err
    assert not fake.calls and not workspace_root().exists()


# -- clarifying questions ------------------------------------------------------------------------


def test_an_unclear_request_nobody_can_answer_stops_with_its_questions_and_exit_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(
        monkeypatch,
        intents=["new_project"],
        needs_clarification=True,
        questions=["Which database?"],
        confidence="low",
    )
    fake = use_runner(monkeypatch)

    assert do(REQUEST, "--yes") == 4

    err = capsys.readouterr().err
    assert "Question 1: Which database?" in err and not fake.calls


def test_a_question_the_model_was_fairly_sure_about_becomes_an_assumption_the_team_is_told(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(
        monkeypatch,
        intents=["new_project"],
        needs_clarification=True,
        questions=["Which database?"],
        confidence="medium",
    )

    assert do(REQUEST, "--dry-run", "--json") == 0

    profile = plan_of(capsys)["profile"]
    assert profile["needs_clarification"] is False
    assert profile["assumptions"] == [
        "Not confirmed (no one to ask); the team goes on its assumption: Which database?"
    ]
    told = do_command.with_notes(REQUEST, TaskProfile.model_validate(profile))
    assert told.startswith(REQUEST) and "Which database?" in told and "not instructions" in told


def test_answers_are_part_of_the_request_the_team_gets() -> None:
    profile = TaskProfile(intents=["feature"], clarifications=["Q: Which db? A: SQLite"])

    told = do_command.with_notes("Add search.", profile)

    assert "Answers to clarifying questions:\n- Q: Which db? A: SQLite" in told
    assert do_command.with_notes("Add search.", TaskProfile(intents=["feature"])) == "Add search."


# -- running -------------------------------------------------------------------------------------


def test_a_new_project_is_built_and_the_summary_says_what_was_done_and_proved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])
    fake = use_runner(monkeypatch)

    code = do(
        REQUEST, "--project-name", "demo", "--strategy", "pipeline", "--checks",
        str(write_checks()), "--yes",
    )  # fmt: skip

    out = capsys.readouterr().out
    assert code == 0, out
    assert fake.calls
    assert "Understood as: new_project on a new project" in out
    assert "Task: new_project: " in out and "(recipe composed-new-project)" in out
    assert "Proof: " in out and "required checks passed (run by the controller)" in out
    assert "Workspace: " in out
    (ref,) = find_runs(workspace_root())
    assert ref.manifest.recipe == "composed-new-project" and ref.manifest.verdict == "verified"
    kept = json.loads((ref.run_dir / do_command.PROFILE_FILE).read_text(encoding="utf-8"))
    assert kept["profile"]["intents"] == ["new_project"] and kept["route"] == "new"


def test_a_single_agent_strategy_builds_without_composed_stages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])

    assert do(REQUEST, "--strategy", "single", "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["ladder"] is None and [s["name"] for s in plan["stages"]] == ["build"]


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_feature_on_a_repository_ends_on_a_branch_with_a_patch_and_the_summary_says_so(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code = do(FEATURE, "--repo", str(legacy), "--yes")

    out = capsys.readouterr().out
    assert code == 0, out
    assert "Task: feature: " in out and "Branch: engineering-team/" in out
    assert "Patch: " in out and "changes.patch" in out and "Report: " in out
    (ref,) = find_runs(workspace_root())
    assert ref.manifest.mode == "feature" and ref.manifest.recipe == "composed-feature"
    assert (ref.run_dir / do_command.PROFILE_FILE).is_file()


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_json_output_names_the_task_the_branch_and_the_patch(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    assert do(FEATURE, "--repo", str(legacy), "--json") == 0  # --json: nobody is asked

    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "verified" and data["exit_code"] == 0
    assert data["task"]["intents"] == ["feature"] and data["task"]["route"] == "repository"
    assert data["isolation"]["branch"].startswith("engineering-team/")
    assert data["patch"].endswith("changes.patch")


# -- review and questions ------------------------------------------------------------------------


@pytest.mark.git
@pytest.mark.parametrize(
    ("intent", "command", "extra"),
    [("review", "review", "--focus"), ("question", "analyze", "--deep")],
)
def test_a_review_and_a_question_use_the_commands_that_already_do_them(
    intent: str,
    command: str,
    extra: str,
    legacy: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    triage_says(monkeypatch, intents=[intent], size="small")
    calls: list[list[str]] = []
    real = cli_app.main

    def main_(argv: Any) -> int:
        calls.append(list(argv))
        return real(argv) if len(calls) == 1 else 0  # the second call is the command `do` hands to

    monkeypatch.setattr(cli_app, "main", main_)

    code = do("Look at the change on this branch.", "--repo", str(legacy), "--yes")

    assert code == 0
    argv = calls[1]
    assert argv[0] == command and "--repo" in argv and extra in argv
    assert argv[argv.index("--repo") + 1] == str(legacy.resolve())


# -- the presets ---------------------------------------------------------------------------------


def test_the_older_commands_are_still_commands() -> None:
    for name in ("new", "feature", "fix", "maintain", "review", "analyze", "do"):
        assert name in cli_app.COMMANDS
