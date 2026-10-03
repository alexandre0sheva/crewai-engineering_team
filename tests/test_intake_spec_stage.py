"""The Product Analyst's spec stage: the contract, ``docs/spec.md``, the one repair, and the
clarifying questions (asked with --interactive, otherwise recorded as assumptions)."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from pipeline_fakes import SPEC, FakeRunner
from test_pipeline_flow import ROOT, only_run, project, run_dir, runs, start, use_runner

from engineering_team import main
from engineering_team.contracts import AcceptanceCriterion, Spec
from engineering_team.pipeline.spec_stage import (
    check_spec,
    needs_clarification,
    render_spec,
    spec_problems,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events
from engineering_team.runtime.interaction import HumanChannel


def spec_with(**changes: object) -> Spec:
    return SPEC.model_copy(update=changes)


def criteria(*ids: str) -> list[AcceptanceCriterion]:
    return [AcceptanceCriterion(id=i, text=f"{i} holds") for i in ids]


def saved_spec() -> Spec:
    state = PipelineState.load(run_dir(only_run()))
    assert state is not None and state.spec is not None
    return state.spec


def events(type_: str) -> list[object]:
    return [e for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == type_]


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class Prompts:
    """What the terminal was asked, and what the person will reply (an empty reply declines)."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.replies: list[str] = []

    def reply(self, prompt: str = "") -> str:
        self.asked.append(prompt)
        return self.replies.pop(0) if self.replies else ""


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Prompts:
    """stdin is a terminal and ``input`` answers from ``terminal.replies``."""

    prompts = Prompts()
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", prompts.reply)
    return prompts


# -- the contract ----------------------------------------------------------------------------


def test_valid_specs_pass_and_problems_name_the_fix() -> None:
    assert spec_problems(SPEC) == []
    cases = {
        "no title": spec_with(title=" "),
        "no acceptance criteria": spec_with(criteria=[]),
        "must look like AC-1": spec_with(criteria=criteria("AC-1", "R2")),
        "used twice": spec_with(criteria=criteria("AC-1", "AC-1")),
        "AC-0": spec_with(criteria=criteria("AC-0")),
        "has no text": spec_with(criteria=[AcceptanceCriterion(id="AC-1", text=" ")]),
    }
    for fragment, spec in cases.items():
        assert fragment in " ".join(spec_problems(spec)), fragment
    with pytest.raises(Exception, match=r"AC-1, AC-2"):
        check_spec(cases["used twice"])


def test_the_rendered_spec_lists_every_criterion_with_its_id_and_the_open_items() -> None:
    spec = spec_with(
        non_goals=["No web UI"],
        assumptions=["Python 3.12"],
        clarifications=["Q: Where? A: Local"],
        open_questions=["Which license?"],
        confidence="medium",
    )

    text = render_spec(spec)

    assert text.startswith("# Notes CLI\n")
    assert "- **AC-1** (functional): add stores a note" in text
    assert "- **AC-2** (functional): list prints notes" in text
    for heading in ("Non-goals", "Assumptions", "Clarifications", "Open questions"):
        assert f"## {heading}" in text
    assert "Analyst confidence: medium" in text
    assert "## Non-goals" not in render_spec(SPEC)  # empty sections are left out


def test_only_low_confidence_or_blocking_questions_need_clarification() -> None:
    assert not needs_clarification(SPEC)
    assert not needs_clarification(spec_with(open_questions=["Which license?"]))
    assert needs_clarification(spec_with(confidence="low"))
    assert needs_clarification(spec_with(blocking_questions=["CLI or web?"]))
    assert not needs_clarification(spec_with(blocking_questions=["  ", ""]))


# -- the stage ---------------------------------------------------------------------------------


def test_the_controller_writes_docs_spec_md_from_the_contract_and_ids_flow_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    text = (project() / "docs" / "spec.md").read_text(encoding="utf-8")
    assert text == render_spec(SPEC)
    assert saved_spec() == SPEC
    plan_request = next(r for r in runner.requests if r.stage.name == "plan")
    assert [c.id for c in plan_request.state.spec.criteria] == ["AC-1", "AC-2"]  # type: ignore[union-attr]


def test_an_invalid_spec_is_repaired_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = spec_with(criteria=criteria("AC-1", "AC-1"))
    runner = use_runner(monkeypatch, FakeRunner(specs=[bad, SPEC]))

    assert start() == 0

    assert [c for c in runner.calls if c[0] == "spec"] == [("spec", None)] * 2
    second = [r for r in runner.requests if r.stage.name == "spec"][1]
    assert "previous attempt failed" in second.note and "AC-1" in second.note
    assert "inspect the workspace" not in second.note  # not the workspace-resume boilerplate
    assert saved_spec() == SPEC


def test_a_spec_that_stays_invalid_fails_the_stage_after_the_one_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(specs=[spec_with(criteria=[])]))

    assert start() == 1

    assert len([c for c in runner.calls if c[0] == "spec"]) == 2
    spec_record = next(r for r in only_run().stages if r.name == "spec")
    assert spec_record.status == "failed" and "no acceptance criteria" in spec_record.detail
    assert not (project() / "docs" / "spec.md").exists()


# -- clarification: no human -------------------------------------------------------------------


def test_without_a_human_blocking_questions_become_recorded_assumptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asking = spec_with(
        confidence="low",
        blocking_questions=["Should it sync to the cloud?"],
        assumptions=["Local files only"],
    )
    runner = use_runner(monkeypatch, FakeRunner(specs=[asking]))

    assert start() == 0

    assert len([c for c in runner.calls if c[0] == "spec"]) == 1  # nothing to revise
    spec = saved_spec()
    assert spec.blocking_questions == []
    assert spec.open_questions == ["Should it sync to the cloud?"]
    assert "Not confirmed (no one to ask), so the team assumes the above: Should it sync" in str(
        spec.assumptions
    )
    assert any("not confident" in a for a in spec.assumptions)
    assert "Should it sync to the cloud?" in (project() / "docs" / "spec.md").read_text()
    assert events("question") == [] and len(events("spec.assumed")) == 1


def test_interactive_without_a_terminal_is_forced_non_interactive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking = spec_with(blocking_questions=["CLI or web?"])
    use_runner(monkeypatch, FakeRunner(specs=[asking]))

    assert start("--interactive") == 0  # pytest's stdin is not a terminal

    assert "running non-interactively" in capsys.readouterr().err
    assert events("question") == []
    assert saved_spec().open_questions == ["CLI or web?"]


def test_stdin_carrying_the_request_forces_non_interactive(
    monkeypatch: pytest.MonkeyPatch, terminal: Prompts
) -> None:
    asking = spec_with(blocking_questions=["CLI or web?"])
    use_runner(monkeypatch, FakeRunner(specs=[asking]))
    monkeypatch.setattr("sys.stdin", Terminal("Build a notes CLI with add and list."))

    code = main.run(
        ["new", "--request-file", "-", "--interactive", "--project-name", "demo", *workspace()]
        + checks()
    )

    assert code == 0 and terminal.asked == []  # nobody was prompted
    assert events("question") == []


def workspace() -> list[str]:
    return ["--workspace-root", str(Path.cwd() / ROOT)]


def checks() -> list[str]:
    from pipeline_fakes import write_checks

    return ["--strategy", "pipeline", "--checks", str(write_checks())]


# -- clarification: with a human ---------------------------------------------------------------


def test_interactive_questions_are_asked_answered_and_folded_into_the_final_spec(
    monkeypatch: pytest.MonkeyPatch, terminal: Prompts
) -> None:
    first = spec_with(
        blocking_questions=["CLI or web?", "Which database?"],
        assumptions=["A CLI", "SQLite"],
    )
    final = spec_with(
        criteria=criteria("AC-1", "AC-2", "AC-3"), assumptions=["The user chose a CLI"]
    )
    runner = use_runner(monkeypatch, FakeRunner(specs=[first, final]))
    terminal.replies += ["a CLI please", ""]  # the second question is declined

    assert start("--interactive") == 0

    asked = [e.data["text"] for e in events("question")]
    assert asked == ["CLI or web?", "Which database?"]
    assert len(events("question.answered")) == 1
    spec_calls = [r for r in runner.requests if r.stage.name == "spec"]
    assert len(spec_calls) == 2
    assert "Q: CLI or web? A: a CLI please" in spec_calls[1].note
    assert "Which database?" in spec_calls[1].note  # told to state an assumption for it
    spec = saved_spec()
    assert [c.id for c in spec.criteria] == ["AC-1", "AC-2", "AC-3"]  # the revised spec
    assert spec.clarifications == ["Q: CLI or web? A: a CLI please"]
    assert spec.blocking_questions == [] and spec.open_questions == ["Which database?"]
    assert "Not confirmed (no answer)" in str(spec.assumptions)
    text = (project() / "docs" / "spec.md").read_text()
    assert "Q: CLI or web? A: a CLI please" in text and "AC-3" in text


def test_a_declined_question_is_recorded_as_an_assumption_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch, terminal: Prompts
) -> None:
    asking = spec_with(blocking_questions=["CLI or web?"])
    runner = use_runner(monkeypatch, FakeRunner(specs=[asking]))
    terminal.replies += [""]

    assert start("--interactive") == 0

    assert len([c for c in runner.calls if c[0] == "spec"]) == 1
    assert len(events("question")) == 1 and events("question.answered") == []
    assert [e.data["outcome"] for e in events("question.unanswered")] == ["skipped"]
    assert "Not confirmed (no answer)" in str(saved_spec().assumptions)


def test_at_most_five_questions_are_asked(
    monkeypatch: pytest.MonkeyPatch, terminal: Prompts
) -> None:
    many = spec_with(blocking_questions=[f"Question {n}?" for n in range(1, 8)])
    use_runner(monkeypatch, FakeRunner(specs=[many]))
    terminal.replies += ["no"] * 5

    assert start("--interactive") == 0

    assert len(events("question")) == 5
    spec = saved_spec()
    assert spec.open_questions == ["Question 6?", "Question 7?"]
    assert len(spec.clarifications) == 5


def test_a_failed_revision_keeps_the_first_spec_and_records_the_answers(
    monkeypatch: pytest.MonkeyPatch, terminal: Prompts
) -> None:
    asking = spec_with(blocking_questions=["CLI or web?"])
    runner = use_runner(monkeypatch, FakeRunner(specs=[asking], fail={"spec": 0}))
    terminal.replies += ["CLI"]
    real = runner.run

    def flaky(request):  # type: ignore[no-untyped-def]
        if request.stage.name == "spec" and request.note:
            raise RuntimeError("the model is down")
        return real(request)

    runner.run = flaky  # type: ignore[method-assign]

    assert start("--interactive") == 0

    spec = saved_spec()
    assert spec.clarifications == ["Q: CLI or web? A: CLI"] and spec.blocking_questions == []
    assert events("spec.revise_failed")


# -- the human channel ---------------------------------------------------------------------------


def test_skip_answers_the_asker_with_nothing_at_once() -> None:
    human = HumanChannel(interactive=True)
    result: list[str | None] = []
    thread = threading.Thread(target=lambda: result.append(human.ask("Which?", timeout=30)))
    thread.start()
    while not human.pending():
        thread.join(timeout=0.01)

    assert human.skip("Q-001") is True
    thread.join(timeout=5)

    assert result == [None] and human.pending() == []
    assert human.skip("Q-001") is False and human.answer("Q-001", "late") is False


def test_runs_are_listed_even_when_the_spec_asked_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner(specs=[spec_with(blocking_questions=["Why?"])]))

    assert start() == 0

    assert len(runs()) == 1
