"""The answer paths of ``do``: a question answered read-only with checked citations, and a plan
that stops before implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from pipeline_fakes import FakeRunner
from repo_fixtures import PYTHON_APP, snapshot, write_tree
from test_cli_do import do, no_triage_model, triage_says

from engineering_team import main
from engineering_team.modes.answer_contracts import ANSWER_FILE, PLAN_FILE, Answer, Citation
from engineering_team.pipeline.stages import StageRequest
from engineering_team.report.collect import build_report
from engineering_team.report.render_html import render_html
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.run_index import find_runs

QUESTION = "How does the notes store add a note?"
STORE = "def add(items, text):\n    return [*items, text]\n"
GOOD = Citation(path="app/store.py", lines="1-2", quote="return [*items, text]")
BAD = Citation(path="app/store.py", lines="1-2", quote="return items + [text]")
GONE = Citation(path="app/gone.py", lines="1", quote="x")
PAGE = "https://docs.example.com/lists"


def answer(*citations: Citation, summary: str = "It appends to a copy [1].") -> Answer:
    return Answer(summary=summary, citations=list(citations), confidence="high")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "notes", PYTHON_APP)


def ask(project: Path, *args: str) -> int:
    return do(QUESTION, "--repo", str(project), "--yes", *args)


def latest() -> Any:
    (ref,) = find_runs(workspace_root())
    return ref


def test_a_question_is_answered_read_only_and_every_citation_is_checked(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["question"])
    fake = use_runner(monkeypatch, FakeRunner(answers=[answer(GOOD)]))
    before = snapshot(project)

    code = ask(project)

    out = capsys.readouterr().out
    assert code == 0, out
    assert [stage for stage, _ in fake.calls] == ["answer"]
    ref = latest()
    assert ref.manifest.mode == "answer" and ref.manifest.verdict == "verified"
    document = (ref.run_dir / ANSWER_FILE).read_text(encoding="utf-8")
    assert "It appends to a copy [1]." in document and "Verdict: **verified**" in document
    assert "`app/store.py:1-2` (verified)" in document
    assert "Proof: 1 of 1 citation(s) verified by the controller" in out
    assert "Sources" in out and "Task: question: " in out  # the answer is shown, then the summary
    assert snapshot(project) == before  # no branch, no files, nothing written
    assert not (project / ANSWER_FILE).exists()


def test_the_json_summary_carries_the_answer_and_its_verification(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["question"])
    use_runner(monkeypatch, FakeRunner(answers=[answer(GOOD)]))

    assert ask(project, "--json") == 0

    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "verified" and data["report"].endswith(ANSWER_FILE)
    assert (
        data["answer"]["verified"] == 1 and data["answer"]["citations"][0]["status"] == "verified"
    )
    assert data["task"]["route"] == "answer"


def test_citations_that_do_not_hold_get_one_more_turn_with_the_reasons(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["question"])
    fake = use_runner(monkeypatch, FakeRunner(answers=[answer(BAD, GONE), answer(GOOD)]))

    assert ask(project) == 0

    assert [stage for stage, _ in fake.calls] == ["answer", "answer"]
    note = fake.requests[1].note
    assert "[1] app/store.py:1-2: the cited lines do not contain the quote" in note
    assert "[2] app/gone.py:1: no such file in the project" in note
    assert latest().manifest.verdict == "verified"


def test_a_wrong_citation_lowers_the_verdict_and_is_flagged_in_the_answer(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["question"])
    use_runner(monkeypatch, FakeRunner(answers=[answer(GOOD, GONE)]))

    code = ask(project)

    assert code == 4  # partial: one source holds, one does not
    ref = latest()
    assert ref.manifest.verdict == "partial" and ref.manifest.status == "failed"
    document = (ref.run_dir / ANSWER_FILE).read_text(encoding="utf-8")
    assert "`app/gone.py:1` (NOT VERIFIED: no such file in the project)" in document
    assert "Verdict: **partial**. 1 of 2 citation(s)" in document
    assert "is partial" in capsys.readouterr().err


def test_an_answer_whose_citations_all_fail_is_failed_and_one_without_any_is_partial(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["question"])
    use_runner(monkeypatch, FakeRunner(answers=[answer(BAD)]))
    assert ask(project) == 3
    assert latest().manifest.verdict == "failed"

    use_runner(monkeypatch, FakeRunner(answers=[answer()]))
    assert ask(project) == 4
    runs = find_runs(workspace_root())
    assert runs[-1].manifest.verdict == "partial"
    assert "cites nothing" in (runs[-1].run_dir / ANSWER_FILE).read_text(encoding="utf-8")


def test_the_answer_agent_cannot_write_the_project_and_a_changed_project_fails_the_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["question"])
    refused: list[str] = []

    def meddle(request: StageRequest) -> None:
        tools = {tool.name: tool for tool in request.tools or ()}
        assert "Write Project File" in tools  # only for its own scratch path
        refused.append(str(tools["Write Project File"].run(path="app/store.py", content="x = 1\n")))
        (project / "app" / "extra.py").write_text("x = 1\n", encoding="utf-8")  # a bypass

    use_runner(monkeypatch, FakeRunner(answers=[answer(GOOD)], on_call=meddle))

    assert ask(project) == 3

    assert refused[0].startswith("ERROR:") and "outside your write scope" in refused[0]
    assert (project / "app" / "store.py").read_text(encoding="utf-8") == STORE
    document = (latest().run_dir / ANSWER_FILE).read_text(encoding="utf-8")
    assert "The project changed during this read-only run: app/extra.py" in document
    assert latest().manifest.verdict == "failed"


def test_a_page_is_cited_only_when_the_run_fetched_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["research"])

    def fetch(request: StageRequest) -> None:
        request.ctx.trajectory.tool_call(
            tool="Fetch URL", args={"url": PAGE}, output=f"Fetched {PAGE} (HTTP 200, text/html, 9)",
            ok=True, ms=3, stage="answer",
        )  # fmt: skip

    page = Citation(url=PAGE, title="Lists")
    unread = Citation(url="https://docs.example.com/unread", title="Unread")
    use_runner(monkeypatch, FakeRunner(answers=[answer(page)], on_call=fetch))
    assert ask(project) == 0

    use_runner(monkeypatch, FakeRunner(answers=[answer(page, unread)], on_call=fetch))
    assert ask(project) == 4
    document = (find_runs(workspace_root())[-1].run_dir / ANSWER_FILE).read_text(encoding="utf-8")
    assert "unread` (NOT VERIFIED: no Fetch URL call of this run read that page)" in document


def test_a_question_with_no_project_runs_in_a_workspace_of_its_own(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["question"])

    def fetch(request: StageRequest) -> None:
        request.ctx.trajectory.tool_call(
            tool="Fetch URL", args={"url": PAGE}, output="", ok=True, ms=1, stage="answer"
        )

    fake = use_runner(monkeypatch, FakeRunner(answers=[answer(Citation(url=PAGE))], on_call=fetch))

    code = do("How do Python lists grow?", "--allow-web", "--yes")

    assert code == 0, capsys.readouterr().out
    assert [stage for stage, _ in fake.calls] == ["answer"]
    ref = latest()
    assert ref.manifest.project_name == "answers" and (ref.run_dir / ANSWER_FILE).is_file()
    assert not (workspace_root() / "answers" / ".git").exists()


def test_the_preview_of_a_question_says_it_is_read_only(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["question"])

    assert do(QUESTION, "--repo", str(project), "--dry-run") == 0

    out = capsys.readouterr().out
    assert "Plan: answer read-only; the controller checks every citation." in out
    assert "1. profile" in out and "2. answer [answer, session]" in out
    assert "Cost: not estimated (read-only)." in out


def test_mode_analyze_still_maps_the_project(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    assert do(QUESTION, "--repo", str(project), "--mode", "analyze", "--dry-run", "--json") == 0

    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["route"] == "analyze" and plan["recipe"] == "adopt"


def test_the_report_shows_the_answer_and_which_citations_held(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["question"])
    use_runner(monkeypatch, FakeRunner(answers=[answer(GOOD, GONE)]))
    ask(project)
    ref = latest()

    report = build_report(ref.run_dir)
    text = render_markdown(report)
    page = render_html(report, ref.run_dir)

    assert "## Answer" in text and "1 of 2 citation(s) verified by the controller" in text
    assert "missing_file ⚠ not proven" in text
    assert 'id="answer"' in page and "missing_file" in page


# -- plan only -----------------------------------------------------------------------------------


def test_a_plan_only_run_stops_before_implementation_and_changes_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["plan_only"], size="medium")
    scopes: list[Any] = []
    fake = use_runner(monkeypatch, FakeRunner(on_call=lambda r: scopes.append(r.write_scope)))
    before = snapshot(project)

    code = do("Plan a search feature for the notes app.", "--repo", str(project), "--yes")

    out = capsys.readouterr().out
    assert code == 0, out
    assert [stage for stage, _ in fake.calls] == ["design"]  # no implement, no verify, no review
    assert scopes[0] is not None and scopes[0].allow == ()  # not even a docs file
    ref = latest()
    plan = (ref.run_dir / PLAN_FILE).read_text(encoding="utf-8")
    assert "# Plan" in plan and "## Risks" in plan and "Nothing was implemented" in plan
    assert "Proof: a plan only: nothing was implemented or run" in out
    assert "Nothing was implemented" in out  # printed for the person
    assert snapshot(project) == before


def test_a_plan_only_run_that_changed_the_project_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["plan_only"], size="medium")
    use_runner(
        monkeypatch,
        FakeRunner(on_call=lambda r: (project / "app" / "new.py").write_text("x = 1\n")),
    )

    assert do("Plan a search feature.", "--repo", str(project), "--yes") == 3

    assert latest().manifest.verdict == "failed"
    assert "app/new.py" in (latest().run_dir / PLAN_FILE).read_text(encoding="utf-8")


def test_questions_and_plans_have_their_documents_in_the_ui_results(project: Path) -> None:
    from engineering_team.ui.routes_board import ARTIFACT_FILES

    names = [name for name, _ in ARTIFACT_FILES]
    assert ANSWER_FILE in names and PLAN_FILE in names
    assert main is not None
