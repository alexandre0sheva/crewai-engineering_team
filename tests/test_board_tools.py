"""The board, notes, and human tools: calls, refusals, events, pausing, and agents using them."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import pytest
from conftest import Toolbox

from engineering_team.board import CONTROLLER, USER
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task
from engineering_team.tools import build_tools

MakeContext = Callable[..., RunContext]
MakeToolbox = Callable[..., Toolbox]


@pytest.fixture
def backend(make_toolbox: MakeToolbox) -> Toolbox:
    box = make_toolbox(groups=["board", "notes", "human"], agent="backend")
    box.ctx.board.create_card("Architecture", kind="stage", stage="Architecture", status="ready")
    box.ctx.board.create_card(
        "API", kind="work_package", assignee="backend", stage="Build", status="ready"
    )
    box.ctx.board.create_card(
        "UI", kind="work_package", assignee="frontend", stage="Build", status="ready"
    )
    return box


# -- board tools -----------------------------------------------------------------------


def test_list_board_cards_shows_progress_then_a_compact_table(backend: Toolbox) -> None:
    result = backend("List Board Cards")

    lines = result.splitlines()
    assert lines[0] == "Board: 0% complete · 0 of 3 cards done"
    assert "ready 3" in lines[1] and "done 0" in lines[1]
    assert lines[2] == "Showing 3 of 3 matching cards:"
    assert lines[3].split() == ["ID", "STATUS", "KIND", "ASSIGNEE", "TITLE"]
    assert lines[5].split() == ["K-002", "ready", "work_package", "backend", "API"]


def test_list_board_cards_filters_and_validates(backend: Toolbox) -> None:
    mine = backend("List Board Cards", mine=True)
    assert "K-002" in mine and "K-003" not in mine and "Showing 1 of 1" in mine
    assert "K-003" in backend("List Board Cards", assignee="frontend")
    assert "Showing 0 of 0" in backend("List Board Cards", status="blocked")
    assert backend("List Board Cards", status="started").startswith("ERROR: Unknown status")
    assert backend("List Board Cards", kind="epic").startswith("ERROR: Unknown kind")
    assert "2 more" in backend("List Board Cards", limit=1)


def test_list_board_cards_hides_user_notes(backend: Toolbox) -> None:
    backend.ctx.board.add_user_note("hello")

    result = backend("List Board Cards")

    assert "hello" not in result and "Showing 3 of 3" in result


def test_get_board_card_shows_the_card_its_comments_and_history(backend: Toolbox) -> None:
    board = backend.ctx.board
    board.comment("K-002", "Please paginate", author=USER)
    board.update("K-002", artifacts=["src/api.py"], criteria_ids=["AC-1"])

    result = backend("Get Board Card", card_id="k-002")

    assert result.splitlines()[0] == "K-002 API"
    assert "status: ready · kind: work_package · assignee: backend · attempts: 0" in result
    assert "artifacts: src/api.py" in result and "criteria: AC-1" in result
    assert "- user: Please paginate" in result
    assert "controller: created -> ready" in result
    assert backend("Get Board Card", card_id="K-404").startswith("ERROR: No card 'K-404'")


def test_an_agent_starts_hands_over_and_cannot_finish_its_own_card(backend: Toolbox) -> None:
    started = backend("Move Card", card_id="K-002", status="in_progress")
    assert started == "Moved K-002: ready -> in_progress (attempt 1). Next: verifying, blocked."

    refused = backend("Move Card", card_id="K-002", status="done")
    assert refused.startswith("ERROR: K-002 cannot move from in_progress to done.")
    assert "Only the controller marks cards done" in refused
    assert backend.ctx.board.get("K-002").status == "in_progress"

    handed = backend("Move Card", card_id="K-002", status="verifying")
    assert "Next: blocked." in handed and "The controller verifies it" in handed


def test_an_agent_cannot_touch_another_agents_card(backend: Toolbox) -> None:
    for name, arguments in (
        ("Move Card", {"card_id": "K-003", "status": "in_progress"}),
        ("Block Card", {"card_id": "K-003", "reason": "x"}),
        ("Report Progress", {"card_id": "K-003", "status_line": "x"}),
        ("Add Subtask", {"parent_id": "K-003", "title": "x"}),
    ):
        assert backend(name, **arguments).startswith("ERROR:"), name
    assert backend.ctx.board.get("K-003").status == "ready"
    assert backend("Comment On Card", card_id="K-003", text="FYI: the API is /v1").startswith(
        "Commented on K-003"
    )


def test_block_and_unblock_record_the_reason(backend: Toolbox) -> None:
    backend("Move Card", card_id="K-002", status="in_progress")

    assert backend("Block Card", card_id="K-002", reason="").startswith("ERROR:")
    blocked = backend("Block Card", card_id="K-002", reason="need the database name")
    assert blocked.startswith("Moved K-002: in_progress -> blocked")
    assert backend.ctx.board.get("K-002").blocked_reason == "need the database name"

    assert backend("Unblock Card", card_id="K-002", note="it is 'app'").startswith(
        "Moved K-002: blocked -> in_progress"
    )
    assert backend.ctx.board.get("K-002").blocked_reason is None


def test_subtasks_comments_and_progress(backend: Toolbox) -> None:
    created = backend("Add Subtask", parent_id="K-002", title="Add pagination", description="d")
    assert created == "Created K-004 (subtask, ready) under K-002."
    assert backend.ctx.board.get("K-004").assignee == "backend"
    assert backend("Add Subtask", parent_id="", title="Loose end").startswith("Created K-005")

    assert backend("Report Progress", card_id="K-002", status_line="models\ndone") == (
        "Progress recorded on K-002: models done"
    )
    assert backend("Report Progress", card_id="K-002", status_line="x" * 201).startswith("ERROR:")
    assert "models done" in backend("List Board Cards", mine=True)


def test_board_tools_log_who_called_them(backend: Toolbox) -> None:
    backend("Move Card", card_id="K-002", status="in_progress")

    events = list(read_events(backend.ctx.run_dir / "events.jsonl"))
    call = next(e for e in events if e.type == "tool.call")
    assert (call.agent, call.data["tool"], call.data["ok"]) == ("backend", "Move Card", True)
    moved = next(e for e in events if e.type == "board.card_moved")
    assert moved.agent == "backend" and moved.data["actor"] == "backend"


def test_the_agent_name_defaults_to_a_generic_actor(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox(groups=["board"])
    card = box.ctx.board.create_card("Solo", kind="work_package", assignee="agent", status="ready")

    assert box("Move Card", card_id=card.id, status="in_progress").startswith("Moved")


# -- notes -----------------------------------------------------------------------------


def test_notes_are_written_read_listed_and_appended(backend: Toolbox) -> None:
    assert backend("List Notes") == "No notes yet. Use Write Note to add one."
    assert backend("Write Note", key="api-contract", text="GET /items").startswith(
        "Saved note 'api-contract' (10 characters)"
    )
    backend("Write Note", key="api-contract", text="POST /items", append=True)
    backend("Write Note", key="naming", text="snake_case")

    assert backend("Read Note", key="api-contract") == "GET /items\n\nPOST /items\n"
    assert backend("List Notes") == "2 note(s):\n- api-contract (24 chars)\n- naming (10 chars)"
    assert (backend.ctx.run_dir / "notes" / "naming.md").read_text(encoding="utf-8") == "snake_case"
    replaced = backend("Write Note", key="naming", text="camelCase")
    assert "(9 characters)" in replaced
    assert backend("Read Note", key="naming") == "camelCase"


def test_note_errors_say_how_to_fix_the_call(backend: Toolbox) -> None:
    backend("Write Note", key="known", text="x")

    assert "Invalid note key" in backend("Write Note", key="../escape", text="x")
    assert "Invalid note key" in backend("Write Note", key="a/b", text="x")
    assert "is empty" in backend("Write Note", key="empty", text="  ")
    assert "limit is 20000" in backend("Write Note", key="big", text="x" * 20_001)
    assert "No note 'missing'. Known notes: known." in backend("Read Note", key="missing")
    assert "Log Decision" in backend("Write Note", key="decisions", text="x")
    assert not (backend.workspace.root / "escape.md").exists()


def test_the_note_count_is_capped(backend: Toolbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("engineering_team.board.notes.MAX_NOTES", 2)
    backend("Write Note", key="one", text="x")
    backend("Write Note", key="two", text="x")

    assert "already 2 notes" in backend("Write Note", key="three", text="x")
    assert backend("Write Note", key="two", text="y").startswith("Saved")  # an update is fine


def test_the_decision_log_is_append_only_and_outlives_runs(
    make_toolbox: MakeToolbox, make_context: MakeContext
) -> None:
    box = make_toolbox("proj", groups=["notes"], agent="architect")
    assert box("Read Note", key="decisions") == "(empty)"
    assert box("Log Decision", decision="Use SQLite:\n single user").startswith("Decision logged")
    box("Log Decision", decision="Serve on port 8000")

    log = box("Read Note", key="decisions")
    assert log.startswith("# Decisions\n")
    assert "(architect) Use SQLite: single user" in log and "(architect) Serve on port 8000" in log
    path = box.workspace.root / ".engineering-team" / "decisions.md"
    assert path.read_text(encoding="utf-8") == log
    # It is a file of the project, not of the run, so a later run sees it.
    later = make_context("proj")
    assert "Use SQLite" in later.notes.read("decisions")


def test_decision_log_limits(backend: Toolbox, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "is empty" in backend("Log Decision", decision="  ")
    assert "limit is 1000" in backend("Log Decision", decision="x" * 1001)
    monkeypatch.setattr("engineering_team.board.notes.MAX_DECISIONS_FILE_CHARS", 60)
    assert backend("Log Decision", decision="short").startswith("Decision logged")
    assert "decision log is full" in backend("Log Decision", decision="another one")


def test_notes_do_not_leak_into_the_project_tree(backend: Toolbox) -> None:
    backend("Write Note", key="secret-plan", text="x")
    backend("Log Decision", decision="x")

    assert not list(backend.workspace.root.glob("*.md"))
    assert (backend.workspace.root / ".engineering-team").is_dir()


# -- ask human -------------------------------------------------------------------------


def test_with_no_human_the_agent_is_told_to_assume(backend: Toolbox) -> None:
    result = backend("Ask Human", question="Postgres or SQLite?")

    assert result.startswith("No human is available to answer - proceed with your best assumption")
    assert "Write Note" in result
    asked = [e for e in read_events(backend.ctx.run_dir / "events.jsonl") if e.type == "question"]
    assert asked[0].data["text"] == "Postgres or SQLite?" and asked[0].data["interactive"] is False
    assert asked[0].agent == "backend"


def test_a_question_is_answered_through_the_channel(backend: Toolbox) -> None:
    channel = backend.ctx.human
    channel.enable()
    answered = threading.Event()

    def human() -> None:
        while not channel.pending():
            threading.Event().wait(0.01)
        (question,) = channel.pending()
        assert (question.agent, question.text) == ("backend", "Which database?")
        assert channel.answer(question.id, "SQLite")
        assert not channel.answer(question.id, "again")  # answered once
        answered.set()

    thread = threading.Thread(target=human)
    thread.start()
    result = backend("Ask Human", question="Which database?", timeout_seconds=10)
    thread.join(timeout=10)

    assert result == "The human answered: SQLite" and answered.is_set()
    assert channel.pending() == [] and not channel.answer("Q-404", "x")
    types = [e.type for e in read_events(backend.ctx.run_dir / "events.jsonl")]
    assert types.count("question") == 1 and types.count("question.answered") == 1


def test_asking_with_a_card_blocks_it_while_waiting(
    backend: Toolbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, channel = backend.ctx.board, backend.ctx.human
    channel.enable()
    backend("Move Card", card_id="K-002", status="in_progress")
    seen: list[str] = []

    def human() -> None:
        while not channel.pending():
            threading.Event().wait(0.01)
        seen.append(board.get("K-002").status + ":" + (board.get("K-002").blocked_reason or ""))
        channel.answer(channel.pending()[0].id, "yes")

    thread = threading.Thread(target=human)
    thread.start()
    result = backend("Ask Human", question="Add auth?", card_id="K-002", timeout_seconds=10)
    thread.join(timeout=10)

    assert seen == ["blocked:waiting for the human: Add auth?"]
    assert result == "The human answered: yes" and board.get("K-002").status == "in_progress"
    assert [m.to_status for m in board.get("K-002").history][-2:] == ["blocked", "in_progress"]


def test_an_unanswered_question_times_out_with_advice(
    backend: Toolbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engineering_team.tools.human_tools.MIN_TIMEOUT", 0.05)
    backend.ctx.human.enable()

    result = backend("Ask Human", question="Anyone there?", timeout_seconds=0)

    assert result.startswith("The human did not answer within 0 seconds - proceed with your best")
    assert backend.ctx.human.pending() == []
    types = [e.type for e in read_events(backend.ctx.run_dir / "events.jsonl")]
    assert "question.unanswered" in types


def test_cancelling_the_run_releases_a_waiting_agent(backend: Toolbox) -> None:
    backend.ctx.human.enable()
    threading.Timer(0.1, backend.ctx.cancel_event.set).start()

    result = backend("Ask Human", question="Anyone?", timeout_seconds=10)

    assert result.startswith("The human did not answer")


def test_an_empty_question_is_rejected(backend: Toolbox) -> None:
    assert backend("Ask Human", question="  ").startswith("ERROR: Ask a specific question")


# -- pausing ---------------------------------------------------------------------------


def test_a_paused_run_holds_every_tool_call_until_resumed(backend: Toolbox) -> None:
    backend.ctx.board.pause()
    results: list[str] = []
    thread = threading.Thread(target=lambda: results.append(backend("List Board Cards")))
    thread.start()
    thread.join(timeout=0.3)
    assert thread.is_alive() and not results

    backend.ctx.board.unpause()
    thread.join(timeout=10)

    assert results and results[0].startswith("Board:")


def test_cancelling_a_paused_run_releases_its_tools_with_an_error(backend: Toolbox) -> None:
    backend.ctx.board.pause()
    results: list[str] = []
    thread = threading.Thread(target=lambda: results.append(backend("List Board Cards")))
    thread.start()
    backend.ctx.cancel_event.set()
    thread.join(timeout=10)

    assert results and results[0].startswith("ERROR: The run was cancelled")


# -- agents driving the tools through a ScriptedLLM ---------------------------------------


def _run(ctx: RunContext, script: list[Any], **tool_options: Any) -> tuple[str, ScriptedLLM]:
    llm = ScriptedLLM(script)
    tools = build_tools(ctx, **tool_options)
    return run_agent_task(ctx, llm, tools=tools, max_iter=25), llm


def test_an_agent_works_a_card_through_the_board_tools(make_context: MakeContext) -> None:
    ctx = make_context()
    ctx.board.create_card("API", kind="work_package", assignee="backend", status="ready")
    script: list[Any] = [
        ToolCall("List Board Cards", {"mine": True}),
        ToolCall("Move Card", {"card_id": "K-001", "status": "in_progress"}),
        ToolCall("Add Subtask", {"parent_id": "K-001", "title": "Write models"}),
        ToolCall("Get Board Card", {"card_id": "K-002"}),
        ToolCall("Report Progress", {"card_id": "K-001", "status_line": "models written"}),
        ToolCall("Comment On Card", {"card_id": "K-001", "text": "Using SQLAlchemy"}),
        ToolCall("Block Card", {"card_id": "K-001", "reason": "need a decision"}),
        ToolCall("Unblock Card", {"card_id": "K-001", "note": "decided"}),
        ToolCall("Move Card", {"card_id": "K-001", "status": "done"}),  # refused
        ToolCall("Move Card", {"card_id": "K-001", "status": "verifying"}),
        "Handed K-001 over for verification.",
    ]

    result, llm = _run(ctx, script, groups=["board"], agent="backend")

    assert result.startswith("Handed K-001")
    llm.assert_exhausted()
    card = ctx.board.get("K-001")
    assert card.status == "verifying" and card.progress_note == "models written"
    assert [c.text for c in card.comments] == ["Using SQLAlchemy"]
    assert "Only the controller marks cards done" in llm.calls[9].prompt  # the refusal reached it
    assert ctx.board.get("K-002").kind == "subtask"


def test_an_agent_shares_notes_and_logs_a_decision(make_context: MakeContext) -> None:
    ctx = make_context()
    script: list[Any] = [
        ToolCall("List Notes", {}),
        ToolCall("Write Note", {"key": "api-contract", "text": "GET /items returns a list"}),
        ToolCall("Read Note", {"key": "api-contract"}),
        ToolCall("Log Decision", {"decision": "Use FastAPI: async and typed"}),
        ToolCall("Read Note", {"key": "decisions"}),
        "Notes saved.",
    ]

    result, llm = _run(ctx, script, groups=["notes"], agent="architect")

    assert result == "Notes saved."
    assert "GET /items returns a list" in llm.calls[3].prompt
    assert "(architect) Use FastAPI" in llm.calls[-1].prompt
    assert ctx.notes.read("api-contract") == "GET /items returns a list"


def test_an_agent_asking_a_human_gets_the_no_human_advice(make_context: MakeContext) -> None:
    ctx = make_context()
    script: list[Any] = [
        ToolCall("Ask Human", {"question": "Should I add a login page?"}),
        ToolCall("Write Note", {"key": "assumptions", "text": "No login page."}),
        "Proceeded without a login page.",
    ]

    result, llm = _run(ctx, script, groups=["human", "notes"], agent="frontend")

    assert result.startswith("Proceeded")
    assert "No human is available" in llm.calls[1].prompt
    assert ctx.notes.read("assumptions") == "No login page."


def test_a_scripted_run_leaves_a_board_whose_history_explains_it(
    make_context: MakeContext,
) -> None:
    ctx = make_context()
    board = ctx.board
    stage = board.create_card("Build", kind="stage", stage="Build", status="ready")
    package = board.create_card(
        "API", kind="work_package", assignee="backend", stage="Build", status="ready"
    )
    board.move(stage.id, "in_progress", actor=CONTROLLER)
    script: list[Any] = [
        ToolCall("Move Card", {"card_id": package.id, "status": "in_progress"}),
        ToolCall("Move Card", {"card_id": package.id, "status": "done"}),  # refused
        ToolCall("Move Card", {"card_id": package.id, "status": "verifying"}),
        "Done; please verify.",
    ]

    _run(ctx, script, groups=["board"], agent="backend")
    board.move(package.id, "in_progress", actor=CONTROLLER, reason="tests failed", evidence=["t"])
    board.move(package.id, "verifying", actor="backend")
    board.move(package.id, "done", actor=CONTROLLER, evidence=["tests"])
    board.move(stage.id, "done", actor=CONTROLLER, stage_success=True)
    ctx.board.flush()

    saved = json.loads((ctx.run_dir / "board.json").read_text(encoding="utf-8"))
    cards = {card["id"]: card for card in saved["cards"]}
    assert [(m["actor"], m["to_status"]) for m in cards[package.id]["history"]] == [
        ("controller", "ready"),
        ("backend", "in_progress"),
        ("backend", "verifying"),
        ("controller", "in_progress"),
        ("backend", "verifying"),
        ("controller", "done"),
    ]
    assert cards[package.id]["attempts"] == 2 and cards[stage.id]["status"] == "done"
    assert "K-001" in (ctx.run_dir / "board.md").read_text(encoding="utf-8")
    kinds = [
        e.type for e in read_events(ctx.run_dir / "events.jsonl") if e.type.startswith("board.")
    ]
    assert kinds.count("board.card_moved") == 7  # every move is in the event log too
    assert USER not in {m["actor"] for card in saved["cards"] for m in card["history"]}
