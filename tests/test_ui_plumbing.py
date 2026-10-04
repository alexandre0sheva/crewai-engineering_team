"""What the web UI needed from the rest of the program: naming a run, answering through the
inbox, per-run overrides, whole cards on board events, the doctor line, and the ``ui`` command."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from cli_helpers import latest_run, new_args, project, use_runner, workspace_root
from pipeline_fakes import FakeRunner

from engineering_team import main
from engineering_team.cli.doctor import run_checks
from engineering_team.runtime.context import RunContext, new_run_id, reserve_run_id
from engineering_team.runtime.events import read_events
from engineering_team.runtime.inbox import apply_pending, pending_commands, post_command
from engineering_team.runtime.run_store import RunStore
from engineering_team.settings import OVERRIDES_ENV, SettingsError, load_settings

MakeContext = Callable[..., RunContext]
RUN_ID = "20261003-101500-abc123"


# -- naming a run ------------------------------------------------------------------------------


def test_a_reserved_run_id_is_used_once() -> None:
    reserve_run_id(RUN_ID)

    assert new_run_id() == RUN_ID
    assert new_run_id() != RUN_ID  # one use, then ids are made again
    reserve_run_id(RUN_ID)
    reserve_run_id(None)
    assert new_run_id() != RUN_ID


@pytest.mark.parametrize("bad", ["", "x", "2026-10-03", "../etc/passwd", f"{RUN_ID}/x", "A" * 22])
def test_only_a_well_formed_run_id_can_be_reserved(bad: str) -> None:
    with pytest.raises(ValueError, match="not a run id"):
        reserve_run_id(bad)


def test_a_run_can_be_named_on_the_command_line() -> None:
    code = main.run(
        ["--workspace-root", str(workspace_root()), "--run-id", RUN_ID, "new", "--request", "x",
         "--project-name", "demo", "--prepare-only"]
    )  # fmt: skip

    assert code == 0
    assert RunStore(project()).load(RUN_ID).run_id == RUN_ID


def test_the_run_id_option_may_follow_the_command() -> None:
    code = main.run(
        ["new", "--request", "x", "--project-name", "demo", "--prepare-only",
         "--workspace-root", str(workspace_root()), "--run-id", RUN_ID]
    )  # fmt: skip

    assert code == 0 and RunStore(project()).load(RUN_ID).run_id == RUN_ID


def test_a_malformed_run_id_is_a_usage_error() -> None:
    code = main.run(["--run-id", "nope", "new", "--request", "x", "--prepare-only"])

    assert code == 2


def test_a_command_without_a_run_id_never_inherits_an_earlier_one() -> None:
    main.run(["--run-id", RUN_ID, "--workspace-root", str(workspace_root()), "runs"])  # unused

    main.run(["--workspace-root", str(workspace_root()), "runs"])

    assert new_run_id() != RUN_ID


def test_the_run_id_options_are_hidden_from_help(capsys: pytest.CaptureFixture[str]) -> None:
    main.run(["--help"])

    assert "--run-id" not in capsys.readouterr().out


def test_answers_via_inbox_lets_the_team_ask_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []
    runner = FakeRunner(on_call=lambda request: seen.append(request.ctx.human.interactive))
    use_runner(monkeypatch, runner)

    assert main.run(new_args("--answers-via-inbox")) == 0
    assert seen and all(seen)
    seen.clear()
    assert main.run(new_args(name="other")) == 0
    assert seen and not any(seen)  # the default: nobody to ask
    assert latest_run()


# -- answering through the inbox -------------------------------------------------------------


def ask_in_thread(ctx: RunContext, text: str) -> tuple[threading.Thread, list[str | None]]:
    got: list[str | None] = []
    thread = threading.Thread(target=lambda: got.append(ctx.human.ask(text, timeout=10)))
    thread.start()
    return thread, got


def wait_pending(ctx: RunContext) -> str:
    import time

    for _ in range(200):
        if pending := ctx.human.pending():
            return pending[0].id
        time.sleep(0.02)
    raise AssertionError("the question never opened")


def test_an_answer_in_the_inbox_reaches_the_asking_agent(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.human.enable(True)
    thread, got = ask_in_thread(ctx, "Which database?")
    question = wait_pending(ctx)

    post_command(ctx.run_dir, "answer", question=question, text=" SQLite ")
    assert apply_pending(ctx) == 1
    thread.join(5)

    assert got == ["SQLite"] and pending_commands(ctx.run_dir) == []


def test_an_empty_answer_declines(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.human.enable(True)
    thread, got = ask_in_thread(ctx, "Which database?")
    question = wait_pending(ctx)

    post_command(ctx.run_dir, "answer", question=question, text="")
    apply_pending(ctx)
    thread.join(5)

    assert got == [None]


def test_an_answer_to_nothing_is_rejected_and_removed(make_context: MakeContext) -> None:
    ctx = make_context()
    post_command(ctx.run_dir, "answer", question="Q-099", text="hello")

    assert apply_pending(ctx) == 0

    rejected = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "inbox.rejected"]
    assert rejected and "no open question Q-099" in rejected[0].data["reason"]
    assert pending_commands(ctx.run_dir) == []


def test_an_answer_names_its_question(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="names the question"):
        post_command(tmp_path, "answer", text="x")


def test_a_malformed_answer_file_is_rejected(make_context: MakeContext) -> None:
    ctx = make_context()
    inbox = ctx.run_dir / "inbox"
    inbox.mkdir()
    (inbox / "00000000000000000001-aa.json").write_text(json.dumps({"kind": "answer", "text": 5}))

    assert apply_pending(ctx) == 0 and pending_commands(ctx.run_dir) == []


# -- per-run overrides in the environment -----------------------------------------------------


def test_overrides_from_the_environment_rank_between_env_and_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_COST_USD", "9")
    monkeypatch.setenv(
        OVERRIDES_ENV, json.dumps({"budget.max_cost_usd": 2, "team.qa.enabled": False})
    )

    settings = load_settings(overrides={"profile": "smoke"})

    assert settings.budget.max_cost_usd == 2  # the JSON beats the plain variable
    assert settings.profile == "smoke" and settings.team["qa"].enabled is False


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [("{nope", "not valid JSON"), ('["a"]', "JSON object"), ('{"budget.surprise": 1}', "surprise"),
     ('{"parallel.max_parallel_agents": 0}', "max_parallel_agents")],
)  # fmt: skip
def test_bad_overrides_are_a_settings_error(
    monkeypatch: pytest.MonkeyPatch, raw: str, fragment: str
) -> None:
    monkeypatch.setenv(OVERRIDES_ENV, raw)

    with pytest.raises(SettingsError, match=fragment):
        load_settings()


def test_blank_overrides_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OVERRIDES_ENV, "   ")

    assert load_settings().budget.max_cost_usd is None


# -- board events ------------------------------------------------------------------------------


def test_every_board_event_carries_the_card_as_it_is_now(make_context: MakeContext) -> None:
    ctx = make_context()
    card = ctx.board.create_card("Build", kind="work_package", assignee="backend", status="ready")
    ctx.board.move(card.id, "in_progress", actor="backend")
    ctx.board.comment(card.id, "A thought", author="user")
    ctx.board.report_progress(card.id, "Halfway", actor="backend")

    events = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type.startswith("board.")]

    assert [e.type for e in events] == [
        "board.card_created", "board.card_moved", "board.card_commented", "board.card_updated",
    ]  # fmt: skip
    for event in events:
        assert event.data["card"]["id"] == card.id
    final = ctx.board.get(card.id).model_dump(mode="json")
    assert events[-1].data["card"] == final  # the last event holds the whole current card
    assert events[1].data["card"]["status"] == "in_progress"
    assert events[2].data["card"]["comments"][0]["text"] == "A thought"


# -- doctor and the ui command ------------------------------------------------------------------


def test_doctor_reports_the_ui_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from engineering_team import ui

    settings = load_settings()
    installed = next(c for c in run_checks(settings, python=(3, 12, 1)) if c.name == "Web UI")
    monkeypatch.setattr(
        ui, "REQUIRED_MODULES", (("fastapi", "fastapi"), ("no_such_module_xyz", "no-such-package"))
    )
    missing = next(c for c in run_checks(settings, python=(3, 12, 1)) if c.name == "Web UI")

    assert installed.status == "ok" and installed.detail
    assert missing.status == "info" and "no-such-package" in missing.detail
    assert "uv sync --extra ui" in missing.hint


def test_ui_without_its_extra_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from engineering_team.cli import ui_command

    monkeypatch.setattr(ui_command, "missing_dependencies", lambda: ["fastapi", "uvicorn"])

    code = main.run(["ui"])

    assert code == 2
    assert "needs fastapi, uvicorn" in capsys.readouterr().err


def test_ui_refuses_a_non_local_address_without_allow_remote(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main.run(["ui", "--host", "0.0.0.0"])

    assert code == 2
    err = capsys.readouterr().err
    assert "--allow-remote" in err and "token" in err


def test_ui_serves_the_app_it_builds(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    import uvicorn

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))

    code = main.run(["--workspace-root", str(workspace_root()), "ui", "--port", "9123"])

    out = capsys.readouterr().out
    assert code == 0 and served["host"] == "127.0.0.1" and served["port"] == 9123
    assert "http://127.0.0.1:9123/api/v1" in out and "Bearer" not in out  # local: no token


def test_ui_beyond_localhost_prints_a_fresh_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn
    from fastapi.testclient import TestClient

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))

    code = main.run(["--json", "ui", "--host", "0.0.0.0", "--allow-remote"])
    token = json.loads(capsys.readouterr().out)["token"]

    assert code == 0 and len(token) >= 40
    client = TestClient(served["app"], base_url="http://192.168.1.5:8765")  # type: ignore[arg-type]
    assert client.get("/api/v1/runs").status_code == 401
    assert (
        client.get("/api/v1/runs", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    )
