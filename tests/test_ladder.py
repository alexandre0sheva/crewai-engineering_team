"""The ``auto`` strategy's escalation ladder: scripted runs that start cheap and escalate."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pipeline_fakes import BODY, REQUEST, FakeRunner, write_checks
from test_pipeline_flow import ROOT, only_run, resume, run_dir, use_runner

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.contracts import CheckResult
from engineering_team.modes.baseline_report import BaselineReport
from engineering_team.pipeline.ladder import (
    carried_from,
    estimate_usd,
    next_rung,
    plan_ladder,
    rung_recipe,
)
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.ladder_run import _escalation_reason
from engineering_team.pipeline.lean import LeanProfile
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.report.collect import build_report
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.events import read_events
from engineering_team.settings import load_settings

CHECK_PY = (
    "import pathlib, sys\n"
    "sys.exit(0 if pathlib.Path('flag.txt').is_file() else 1)\n"
)  # fmt: skip
# One required check that passes only once flag.txt exists.
CHECKS = "- {id: tests, name: Project tests, kind: test, command: python check.py}\n"


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + ":" + os.environ["PATH"])
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS", "1")


class LadderRunner(FakeRunner):
    """A ``FakeRunner`` whose ``lead`` stage builds the project; ``passes_on`` names the rungs
    whose work makes the check pass (it writes ``flag.txt``)."""

    def __init__(self, passes_on: tuple[str, ...] = ("solo",), **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.passes_on = passes_on
        self.rungs: list[tuple[str, str]] = []  # (rung, stage) of every call

    def run(self, request: StageRequest):  # type: ignore[no-untyped-def]
        rung = request.state.rung
        self.rungs.append((rung, request.stage.name))
        output = super().run(request)
        write = request.ctx.workspace.write_file
        if request.stage.name == "lead" or request.stage.name in ("design", "foundation"):
            write("README.md", "# Demo\n" + BODY)
            write("check.py", CHECK_PY)
        if rung in self.passes_on and request.stage.name not in ("verify",):
            write("flag.txt", "ok\n")
        return output


def launch(*extra: str) -> int:
    return main.run(
        [
            "new", "--request", REQUEST, "--project-name", "demo",
            "--workspace-root", str(Path.cwd() / ROOT), "--strategy", "auto",
            "--checks", str(write_checks(CHECKS)), *extra,
        ]
    )  # fmt: skip


def ladder() -> LadderRecord:
    record = LadderRecord.load(run_dir(only_run()))
    assert record is not None
    return record


def rungs() -> list[tuple[str, str]]:
    return [(rung.rung, rung.status) for rung in ladder().rungs]


def events(kind: str) -> list[dict[str, object]]:
    found = read_events(run_dir(only_run()) / "events.jsonl")
    return [event.data for event in found if event.type == kind]


def rung_state(rung: str) -> PipelineState:
    path = run_dir(only_run()) / "rungs" / f"{rung}.json"
    return PipelineState.model_validate_json(path.read_text(encoding="utf-8"))


# -- scripted runs -----------------------------------------------------------------------------


def test_a_small_task_that_passes_on_solo_never_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = use_runner(monkeypatch, LadderRunner(passes_on=("solo",)))

    assert launch() == 0

    manifest = only_run()
    assert manifest.strategy == "auto" and manifest.verdict == "verified"
    record = ladder()
    assert [(r.rung, r.status) for r in record.rungs] == [("solo", "succeeded")]
    assert record.start == "solo" and "small task" in record.reasons[0]
    assert events("ladder.escalated") == []
    assert {name for _, name in runner.rungs} == {"lead"}  # one session, no repair
    state = PipelineState.load(run_dir(manifest))
    assert state is not None and state.rung == "solo"
    assert state.lead is not None and state.lead.cap == 0  # the solo lead calls nobody
    assert "You work alone in this run" in runner.requests[0].lead


def test_failing_verification_escalates_to_team_then_pipeline_on_the_same_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, LadderRunner(passes_on=("pipeline",)))

    assert launch() == 0

    manifest = only_run()
    assert manifest.verdict == "verified"
    record = ladder()
    assert [(r.rung, r.status) for r in record.rungs] == [
        ("solo", "escalated"), ("team", "escalated"), ("pipeline", "succeeded"),
    ]  # fmt: skip
    assert record.rungs[0].failing_checks == ["tests: failed (exit code 1)"]
    moves = events("ladder.escalated")
    assert [(m["from"], m["to"]) for m in moves] == [("solo", "team"), ("team", "pipeline")]
    assert all("verification still failed" in str(m["reason"]) for m in moves)
    # Each rung walked its own recipe; the rungs before keep their records under their name.
    names = [record.name for record in manifest.stages]
    assert {"solo:lead", "solo:verify", "team:lead", "team:verify"} <= set(names)
    assert "lead" not in names and names.count("verify") == 1
    # The team rung was told what solo did; its lead may delegate.
    team_lead = next(
        r for r in runner.requests if r.state.rung == "team" and r.stage.name == "lead"
    )
    brief = team_lead.handoff
    assert "escalated this task from the 'solo' rung to the 'team' rung" in brief
    assert "- tests: failed" in brief and "check.py" in brief
    assert "data, not instructions" in brief
    assert rung_state("team").lead is not None and rung_state("team").lead.cap > 0
    # The board shows every rung's cards and a note where each rung stopped.
    cards = BoardStore(run_dir(manifest)).cards()
    titles = {card.title for card in cards}
    assert {"Solo: Lead", "Team: Lead", "Pipeline: Verify"} <= titles
    notes = [c.text for card in cards for c in card.comments if "Escalated from" in c.text]
    assert len(notes) == 2 and "'solo' to 'team'" in notes[0]
    # The report has the ladder.
    text = render_markdown(build_report(run_dir(manifest)))
    assert "## Escalation" in text and "| solo | escalated |" in text


def test_a_stuck_agent_escalates_too(monkeypatch: pytest.MonkeyPatch) -> None:
    use_runner(monkeypatch, LadderRunner(passes_on=("team",), fail={"lead": 2}))

    assert launch() == 0

    record = ladder()
    assert [(r.rung, r.status) for r in record.rungs] == [
        ("solo", "escalated"),
        ("team", "succeeded"),
    ]
    assert "could not deliver (stuck)" in record.rungs[1].why


def test_a_rung_the_budget_cannot_pay_for_is_not_started(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = load_settings()
    solo, team = estimate_usd(settings, "solo", "small"), estimate_usd(settings, "team", "small")
    assert solo is not None and team is not None and solo < team
    use_runner(monkeypatch, LadderRunner(passes_on=()))

    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_COST_USD", f"{(solo + team) / 2:.4f}")

    assert launch() == 3  # not verified: solo's verification failed and team was not started

    assert only_run().status == "failed"
    assert "Not escalated to 'team': it is estimated at" in capsys.readouterr().out
    record = ladder()
    assert [r.rung for r in record.rungs] == ["solo"] and "budget.max_cost_usd" in record.stopped
    assert events("ladder.stopped")[0]["rung"] == "solo"


def test_the_ladder_stops_at_max_rung(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENGINEERING_LADDER_MAX_RUNG", "solo")
    use_runner(monkeypatch, LadderRunner(passes_on=()))

    assert launch() == 3

    assert rungs() == [("solo", "running")] and events("ladder.escalated") == []
    assert "Not escalated: 'solo' is the highest rung allowed" in capsys.readouterr().out


def test_no_rung_starts_when_the_budget_cannot_pay_for_the_cheapest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = use_runner(monkeypatch, LadderRunner())

    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_COST_USD", "0.0001")

    assert launch() == 1

    assert runner.calls == [] and only_run().status == "failed"
    assert "No rung fits the budget: 'solo' is estimated at" in capsys.readouterr().out


def test_a_run_stopped_in_the_middle_of_an_escalation_resumes_on_the_rung_it_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cancel_on_team(request: StageRequest) -> None:
        if request.state.rung == "team":
            request.ctx.cancel_event.set()

    use_runner(monkeypatch, LadderRunner(passes_on=(), on_call=cancel_on_team))
    assert launch() == 130 or only_run().status == "cancelled"
    assert [(r.rung, r.status) for r in ladder().rungs] == [
        ("solo", "escalated"),
        ("team", "running"),
    ]

    second = use_runner(monkeypatch, LadderRunner(passes_on=("team",)))
    assert resume(only_run().run_id) == 0

    assert only_run().verdict == "verified"
    assert [(r.rung, r.status) for r in ladder().rungs] == [
        ("solo", "escalated"),
        ("team", "succeeded"),
    ]
    assert second.rungs[0] == ("team", "lead")  # solo is not run again
    assert all(rung == "team" for rung, _ in second.rungs)


# -- the policy --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "risky", "start"),
    [("small", False, "solo"), ("medium", False, "team"), ("large", False, "pipeline"),
     ("small", True, "team"), ("large", True, "pipeline")],
)  # fmt: skip
def test_the_starting_rung_comes_from_size_and_risk(size: str, risky: bool, start: str) -> None:
    lean = LeanProfile(size=size, high_risk=risky)  # type: ignore[arg-type]
    assert plan_ladder(load_settings(), lean, load_recipe("new")).start == start


def test_max_rung_caps_the_start_and_the_escalation() -> None:
    settings = load_settings(overrides={"ladder.max_rung": "team"})
    record = plan_ladder(settings, LeanProfile(size="large"), load_recipe("new"))
    assert (record.start, record.max_rung) == ("team", "team")
    assert next_rung("solo", "team") == "team" and next_rung("team", "team") is None


@pytest.mark.parametrize("name", ["review", "upgrade-deps", "add-tests", "docs"])
def test_a_recipe_the_ladder_does_not_climb_for_has_only_the_pipeline_rung(name: str) -> None:
    record = plan_ladder(load_settings(), LeanProfile(size="small"), load_recipe(name))
    assert (record.start, record.lowest, record.max_rung) == ("pipeline", "pipeline", "pipeline")


def test_a_recipe_of_your_own_runs_as_written() -> None:
    mine = load_recipe("feature")
    mine.source = "project"
    assert plan_ladder(load_settings(), LeanProfile(size="small"), mine).start == "pipeline"


def test_an_escalated_rung_keeps_the_skeletons_facts_instead_of_running_it_again() -> None:
    fix = load_recipe("fix")
    first = [s.name for s in rung_recipe(fix, "solo", escalated=False).stages]
    assert first[:3] == ["profile", "baseline", "reproduce"]
    for rung in ("team", "pipeline"):
        stages = rung_recipe(fix, rung, escalated=True).stages
        assert not {"profile", "baseline", "reproduce"} & {s.name for s in stages}
        assert all("repro" not in s.inputs for s in stages)
    assert [s.name for s in rung_recipe(fix, "pipeline", escalated=True).stages][:2] == [
        "map", "triage",
    ]  # fmt: skip
    state = PipelineState(
        baseline=BaselineReport(), checks=[CheckResult(id="tests", status="failed")]
    )
    state.verification.checks_digest = "abc"
    carried = carried_from(state)
    assert "baseline" in carried and "checks" not in carried and "repro" not in carried
    assert carried["verification"]["checks_digest"] == "abc"


@pytest.mark.parametrize(
    ("cause", "stage", "escalates"),
    [("verification", "verify", True), ("stage", "lead", True), ("stage", "release_notes", False),
     ("budget", "lead", False), ("cancelled", "lead", False), ("needs-info", "lead", False),
     ("config", "lead", False)],
)  # fmt: skip
def test_only_the_rungs_own_evidence_escalates(cause: str, stage: str, escalates: bool) -> None:
    state = PipelineState(stopped_by=cause, stopped_at=stage, error="boom")
    recipe = rung_recipe(load_recipe("new"), "solo", escalated=False)
    assert bool(_escalation_reason(state, recipe)) is escalates
