"""The results page's views, from the controller's records: the answer with its checked sources
(good and bad), a stored plan, an escalation that stopped, attempts where none passed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from engineering_team.modes.answer_contracts import (  # noqa: E402
    Answer,
    AnswerCheck,
    AnswerSection,
    Citation,
    CitationResult,
)
from engineering_team.pipeline.attempts_contracts import (  # noqa: E402
    AttemptRecord,
    AttemptsRecord,
    Selection,
)
from engineering_team.pipeline.ladder_contracts import LadderRecord, RungRecord  # noqa: E402
from engineering_team.ui import result_views  # noqa: E402


def report(answer: Answer | None = None, check: AnswerCheck | None = None) -> SimpleNamespace:
    return SimpleNamespace(answer=answer, answer_check=check)


def result(number: int, status: str, detail: str = "") -> CitationResult:
    return CitationResult(
        number=number, kind="code", target=f"f{number}.py:1", status=status, detail=detail
    )  # type: ignore[arg-type]


def test_an_answer_with_a_citation_that_does_not_hold_is_marked_bad_and_says_why(
    tmp_path: Path,
) -> None:
    answer = Answer(
        summary="It parses [1] then writes [2] [9].",
        sections=[AnswerSection(heading="Flow", body="First [1]. Then [2].")],
        citations=[
            Citation(path="f1.py", lines="1", quote="def parse():"),
            Citation(path="f2.py", lines="3", quote="def  write():\n  pass"),
        ],
        confidence="high",
    )
    check = AnswerCheck(
        results=[result(1, "verified"), result(2, "quote_mismatch", "line 3 reads 'def save():'")],
        changed=["README.md"],
    )

    view = result_views.answer_view(report(answer, check), SimpleNamespace(run_dir=tmp_path))

    assert view is not None
    assert view["tone"] == "bad" and view["verified"] == 1 and view["total"] == 2
    assert view["headline"] == "1 of 2 citation(s) verified by the controller"
    bad = view["citations"][1]
    assert bad["ok"] is False and bad["status"] == "quote_mismatch"
    assert bad["detail"] == "line 3 reads 'def save():'" and bad["quote"] == "def write(): pass"
    assert view["changed"] == ["README.md"]
    assert view["sections"][0]["parts"] == [
        {"text": "First "}, {"cite": 1}, {"text": ". Then "}, {"cite": 2}, {"text": "."},
    ]  # fmt: skip


def test_an_answer_that_cites_nothing_says_nothing_was_checked(tmp_path: Path) -> None:
    view = result_views.answer_view(
        report(Answer(summary="Because."), AnswerCheck()), SimpleNamespace(run_dir=tmp_path)
    )

    assert view is not None and view["total"] == 0
    assert view["headline"] == "The answer cites nothing, so nothing in it was checked"


def test_markers_that_name_no_source_stay_plain_text() -> None:
    assert result_views.with_citations("See [3] and [1].", 1) == [
        {"text": "See [3] and "}, {"cite": 1}, {"text": "."},
    ]  # fmt: skip
    assert result_views.with_citations("", 2) == []


def test_a_plan_only_run_shows_its_stored_plan(tmp_path: Path) -> None:
    (tmp_path / "PLAN.md").write_text("# Plan\n\n- do it\n", encoding="utf-8")

    view = result_views.answer_view(report(), SimpleNamespace(run_dir=tmp_path))

    assert view is not None and view["kind"] == "plan" and view["text"].startswith("# Plan")
    assert view["truncated"] is False
    assert result_views.answer_view(report(), SimpleNamespace(run_dir=tmp_path / "x")) is None


def rung(name: str, status: str = "escalated", **fields: object) -> RungRecord:
    return RungRecord(rung=name, recipe="r", why=f"why {name}", status=status, **fields)  # type: ignore[arg-type]


def test_an_escalation_that_could_not_climb_further_says_why_it_stopped() -> None:
    ladder = LadderRecord(
        start="solo",
        max_rung="team",
        reasons=["small task"],
        rungs=[rung("solo", failing_checks=["tests"], cost_usd=0.05), rung("team", "running")],
        stopped="Not escalated: 'team' is the highest rung allowed.",
    )

    view = result_views.escalation_view(ladder)

    assert view is not None and view["climbed"] is True
    assert view["headline"] == "Escalated: solo → team"
    assert view["rungs"][0]["cost"] == "$0.0500" and view["rungs"][0]["failing_checks"] == ["tests"]
    assert view["rungs"][1]["tone"] == "info" and view["stopped"].startswith("Not escalated")


def test_a_run_that_never_climbed_says_no_escalation_was_needed() -> None:
    view = result_views.escalation_view(
        LadderRecord(start="solo", max_rung="pipeline", rungs=[rung("solo", "succeeded")])
    )

    assert view is not None and view["climbed"] is False
    assert view["headline"] == "Stayed on the solo rung: no escalation was needed"
    assert result_views.escalation_view(None) is None


def test_attempts_where_none_passed_show_the_closest_and_never_call_it_verified() -> None:
    record = AttemptsRecord(
        count=2,
        vary="seed",
        attempts=[
            AttemptRecord(label="a1", run_id="r-a1", vary="seed 1", status="failed",
                          checks_passed=1, checks_total=2, failing_checks=["tests"]),
            AttemptRecord(label="a2", run_id="r-a2", vary="seed 2", status="failed",
                          checks_passed=0, checks_total=2),
        ],
        selection=Selection(method="none-passed", chosen="a1", reason="a1 came closest."),
    )  # fmt: skip

    view = result_views.attempts_panel(record)

    assert view is not None and view["method"] == "none-passed"
    assert "closest is kept, unverified" in view["method_text"]
    assert [(a["label"], a["chosen"], a["tone"]) for a in view["attempts"]] == [
        ("a1", True, "bad"), ("a2", False, "bad"),
    ]  # fmt: skip
    assert view["attempts"][0]["checks"] == "1 of 2" and view["judge"] is None
    assert view["reason"] == "a1 came closest."


def test_attempts_that_did_not_finish_have_no_choice_yet() -> None:
    view = result_views.attempts_panel(AttemptsRecord(count=3, vary="model"))

    assert view is not None and view["chosen"] is None and view["method_text"] == "not finished"
    assert result_views.attempts_panel(None) is None
