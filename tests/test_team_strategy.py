"""The team strategy end to end: a lead on ScriptedLLM calls teammates through the real tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from pipeline_fakes import REQUEST, write_checks
from test_baseline import interpreter_on_path  # noqa: F401  (autouse: the repro needs pytest)
from test_pipeline_crews import write
from test_pipeline_flow import ROOT, only_run, project, run_dir

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.contracts import Finding, ReviewReport, RunManifest
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import CrewStageRunner, StageOutput, StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.pricing import build_table
from engineering_team.report.collect import build_report
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.events import read_events
from engineering_team.runtime.profile import build_profile
from engineering_team.runtime.run_store import RunStore
from engineering_team.testing import ScriptedLLM, ToolCall, Turn

LEAD = "generalist_engineer"


def install(monkeypatch: pytest.MonkeyPatch, llms: dict[str, ScriptedLLM]) -> None:
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )


def run_team(*extra: str, request: str = REQUEST) -> int:
    return main.run(
        [
            "new",
            "--request",
            request,
            "--project-name",
            "demo",
            "--workspace-root",
            str(Path.cwd() / ROOT),
            "--strategy",
            "team",
            "--checks",
            str(write_checks()),
            *extra,
        ]
    )


def delegate(teammate: str, paths: list[str], text: str = "Build this part.") -> ToolCall:
    return ToolCall(
        "Delegate", {"teammate": teammate, "brief": text, "owned_paths": paths, "why": "a part"}
    )


def state_of(manifest: RunManifest) -> PipelineState:
    state = PipelineState.load(run_dir(manifest))
    assert state is not None
    return state


def statuses(manifest: RunManifest) -> dict[str, str]:
    return {record.name: record.status for record in manifest.stages}


def told(llm: ScriptedLLM, call: int) -> str:
    """What the model was sent on its ``call``-th call (the tool results so far are in it)."""

    return llm.calls[call].prompt


def test_a_small_task_the_lead_does_alone_is_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    llms = {LEAD: ScriptedLLM([write("README.md"), write("src/notes.py", "X = 1\n"), "Built."])}
    install(monkeypatch, llms)

    assert run_team() == 0

    manifest = only_run()
    assert manifest.strategy == "team" and manifest.verdict == "verified"
    assert statuses(manifest) == {
        "lead": "succeeded",
        "verify": "succeeded",
        "security_review": "skipped",
        "release_notes": "succeeded",
    }
    profile = build_profile(run_dir(manifest), build_table({}))
    assert len(profile.sessions) == 1 and profile.calls == 3
    tools = {t["function"]["name"] for t in llms[LEAD].calls[0].tools or []}
    assert {"delegate", "delegate_parallel", "ask_reviewer"} <= tools
    assert "(up to 2 jobs in this run" in told(llms[LEAD], 0)  # a small task
    assert state_of(manifest).delegations == []


def test_one_delegation_is_a_card_a_lane_and_a_line_in_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = {
        LEAD: ScriptedLLM(
            [delegate("backend_engineer", ["src/storage/**"]), write("README.md"), "Done."]
        ),
        "backend_engineer": ScriptedLLM(
            [
                write("README.md"),  # outside its paths: refused
                write("src/storage/main.py", "VALUE = 1\n"),
                "Storage done.",
            ]
        ),
    }
    install(monkeypatch, llms)

    assert run_team() == 0

    manifest = only_run()
    (record,) = state_of(manifest).delegations
    assert (record.teammate, record.status, record.files_changed) == (
        "backend_engineer", "succeeded", ["src/storage/main.py"],
    )  # fmt: skip
    assert "outside your write scope" in told(llms["backend_engineer"], 1)
    result = told(llms[LEAD], 1)
    assert (
        "D1 backend_engineer succeeded" in result and "Files changed: src/storage/main.py" in result
    )
    assert "The controller's checks after this: Verified" in result
    # The teammate got the brief and the hand-off, not the lead's conversation.
    brief = told(llms["backend_engineer"], 0)
    assert "Build this part." in brief and "You may change only: src/storage/**" in brief

    board = BoardStore(run_dir(manifest))
    (card,) = [c for c in board.cards() if c.kind == "delegation"]
    assert (card.status, card.assignee, card.lane) == ("done", "backend_engineer", "D1")
    events = list(read_events(run_dir(manifest) / "events.jsonl"))
    lanes = [(e.type, e.lane) for e in events if e.type.startswith("lane.")]
    assert lanes == [("lane.started", "D1"), ("lane.finished", "D1")]
    assert [e.data["teammate"] for e in events if e.type == "delegation.started"] == [
        "backend_engineer"
    ]

    report = build_report(run_dir(manifest))
    assert [lane.lane for lane in report.lanes] == ["D1"]
    text = render_markdown(report)
    assert (
        "## Delegations" in text
        and "| D1 | backend_engineer | delegate | succeeded | a part |" in text
    )
    profile = build_profile(run_dir(manifest), build_table({}))
    assert len(profile.sessions) == 2  # the lead and the one teammate it called


def test_parallel_jobs_run_in_lanes_and_an_overlapping_call_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parallel(*jobs: tuple[str, list[str]]) -> ToolCall:
        return ToolCall(
            "Delegate Parallel",
            {
                "briefs": [
                    {"teammate": t, "brief": f"Build {p[0]}.", "owned_paths": p} for t, p in jobs
                ]
            },
        )

    llms = {
        LEAD: ScriptedLLM(
            [
                parallel(("backend_engineer", ["src/**"]), ("frontend_engineer", ["src/web/**"])),
                parallel(("backend_engineer", ["api/**"]), ("frontend_engineer", ["web/**"])),
                write("README.md"),
                "Integrated both parts.",
            ]
        ),
        "backend_engineer": ScriptedLLM([write("api/main.py", "A = 1\n"), "API done."]),
        "frontend_engineer": ScriptedLLM([write("web/index.html", "<p>hi</p>\n"), "UI done."]),
    }
    install(monkeypatch, llms)

    assert run_team() == 0

    assert "may overlap" in told(llms[LEAD], 1)
    manifest = only_run()
    records = state_of(manifest).delegations
    assert [(r.id, r.teammate, r.files_changed) for r in records] == [
        ("D1", "backend_engineer", ["api/main.py"]),
        ("D2", "frontend_engineer", ["web/index.html"]),
    ]
    events = list(read_events(run_dir(manifest) / "events.jsonl"))
    assert sorted(str(e.lane) for e in events if e.type == "lane.started") == ["D1", "D2"]
    assert "Delegations used: 2 of 2." in told(llms[LEAD], 2)


def test_the_cap_refuses_a_job_past_it_and_zero_leaves_the_lead_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_MAX_DELEGATIONS", "1")
    llms = {
        LEAD: ScriptedLLM(
            [
                ToolCall("Ask Architect", {"question": "One module or two?"}),
                ToolCall("Ask Reviewer", {}),
                write("README.md"),
                "Done.",
            ]
        ),
        "solution_architect": ScriptedLLM(["One module."]),
    }
    install(monkeypatch, llms)
    assert run_team() == 0
    assert "Delegation limit reached: 1 of 1 used" in told(llms[LEAD], 2)

    monkeypatch.setenv("ENGINEERING_MAX_DELEGATIONS", "0")
    alone = {LEAD: ScriptedLLM([write("README.md"), "Done."])}
    install(monkeypatch, alone)
    assert run_team("--force-reset") == 0
    tools = {t["function"]["name"] for t in alone[LEAD].calls[0].tools or []}
    assert not tools & {"delegate", "ask_reviewer", "delegate_parallel"}
    assert "You work alone in this run" in told(alone[LEAD], 0)


def test_a_budget_stop_inside_a_delegation_ends_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_TOKENS", "200")
    llms = {
        LEAD: ScriptedLLM([delegate("backend_engineer", ["src/**"]), "Stopping."]),
        "backend_engineer": ScriptedLLM(
            [Turn(write("src/a.py", "A = 1\n"), prompt_tokens=500), write("src/b.py"), "Partial."]
        ),
    }
    install(monkeypatch, llms)

    assert run_team() != 0

    manifest = only_run()
    assert manifest.status == "failed"
    assert "Budget exceeded" in state_of(manifest).error
    (record,) = state_of(manifest).delegations
    assert record.status == "cancelled" and "Budget exceeded" in record.error
    assert "Stop working and summarize" in told(llms[LEAD], 1)
    assert not (project() / "src" / "b.py").exists()  # the teammate's later writes were refused


def test_the_controller_reviews_a_security_sensitive_change_the_lead_did_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = Finding(severity="low", summary="Passwords are compared with ==.", file="src/auth.py")
    llms = {
        LEAD: ScriptedLLM([write("README.md"), write("src/auth.py", "OK = 1\n"), "Built login."]),
        "security_engineer": ScriptedLLM(
            [ReviewReport(summary="One note.", findings=[finding]).model_dump_json()]
        ),
    }
    install(monkeypatch, llms)

    assert run_team(request="Build a tiny notes CLI with a login command and passwords.") == 0

    manifest = only_run()
    assert statuses(manifest)["security_review"] == "succeeded"
    state = state_of(manifest)
    assert state.lead is not None and "auth (request)" in state.lead.signals
    assert [f.summary for f in state.findings] == ["Passwords are compared with ==."]


def test_a_security_review_the_lead_asked_for_after_its_last_change_is_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llms = {
        LEAD: ScriptedLLM(
            [write("README.md"), ToolCall("Ask Security Review", {"focus": "login"}), "Reviewed."]
        ),
        "security_engineer": ScriptedLLM([ReviewReport(summary="Fine.").model_dump_json()]),
    }
    install(monkeypatch, llms)

    assert run_team(request="Build a tiny notes CLI with a login command.") == 0

    manifest = only_run()
    assert statuses(manifest)["security_review"] == "skipped"
    assert [r.kind for r in state_of(manifest).delegations] == ["security_review"]


# -- resume -------------------------------------------------------------------------------------


class Crash(KeyboardInterrupt):
    """The process dies in the middle of a delegated job."""


class LeadRunner:
    """A stage runner without a model: the lead delegates once through the controller, the job
    either crashes (the first run) or finishes; the verify stage never needs a repair."""

    def __init__(self, *, crash: bool, delegate_again: bool = True) -> None:
        self.crash, self.delegate_again = crash, delegate_again
        self.requests: list[StageRequest] = []

    def run(self, request: StageRequest) -> StageOutput:
        self.requests.append(request)
        ctx = request.ctx
        if request.brief:  # a delegated job
            if self.crash:
                raise Crash
            ctx.workspace.write_file("src/store.py", "VALUE = 1\n")
            return StageOutput(summary="Store done.")
        if request.stage.kind == "lead":
            if self.delegate_again:
                delegator = ctx.delegation.active
                assert delegator is not None
                delegator.handle(
                    "Delegate",
                    {"teammate": "backend_engineer", "brief": "Store.", "owned_paths": ["src/**"]},
                )
            ctx.workspace.write_file("README.md", "# Demo\n" + "Real content here. " * 5)
            return StageOutput(summary="Lead done.")
        raise AssertionError(f"unexpected call for {request.stage.name}")


def test_a_run_that_died_mid_delegation_resumes_without_losing_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: LeadRunner(crash=True))
    assert run_team() != 0  # interrupted
    run_id = only_run().run_id

    def as_if_killed(manifest: RunManifest) -> None:
        manifest.status, manifest.finished = "running", None

    RunStore(project()).update(run_id, as_if_killed)
    assert state_of(only_run()).delegations[0].status == "running"
    second = LeadRunner(crash=False)
    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: second)

    assert (
        main.run(
            ["resume", run_id, "--project-name", "demo", "--workspace-root", str(Path.cwd() / ROOT)]
        )
        == 0
    )

    manifest = only_run()
    assert manifest.status == "succeeded" and manifest.resumes == 1
    records = state_of(manifest).delegations
    assert [(r.id, r.status) for r in records] == [("D1", "interrupted"), ("D2", "succeeded")]
    lead_request = next(r for r in second.requests if r.stage.kind == "lead")
    assert "D1 delegate -> backend_engineer: interrupted" in lead_request.note
    board = BoardStore(run_dir(manifest))
    kinds = {c.title[:2]: c.status for c in board.cards() if c.kind == "delegation"}
    assert kinds == {"D1": "cancelled", "D2": "done"}


# -- a repository mode ----------------------------------------------------------------------------


@pytest.mark.git
def test_a_fix_led_by_the_lead_keeps_the_red_to_green_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from repo_fixtures import make_repo
    from test_feature_mode import latest
    from test_fix_mode import BUGGY, FIXED, GOOD, REGRESSION, REPORT
    from typer.testing import CliRunner

    from engineering_team.cli.app import app

    monkeypatch.setenv("ENGINEERING_STRATEGY", "team")
    repo = make_repo(tmp_path / "notes", BUGGY)
    llms = {
        LEAD: ScriptedLLM(
            [
                # the reproduce stage: the controller requires red before anyone changes code
                write("tests/test_regression.py", REGRESSION),
                GOOD.model_dump_json(),
                # the lead: told about the reproduction, it may not edit it
                write("tests/test_regression.py", "def test_nothing():\n    pass\n"),
                write("app/store.py", FIXED),
                "Fixed add: it keeps the earlier notes.",
            ]
        )
    }
    install(monkeypatch, llms)
    command = ["--workspace-root", str(Path.cwd() / ROOT), "fix", "--repo", str(repo)]

    result = CliRunner().invoke(app, [*command, "--request", REPORT])

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.strategy == "team"
    assert [r.name for r in ref.manifest.stages if r.status == "succeeded"] == [
        "profile", "baseline", "reproduce", "lead", "verify", "summary",
    ]  # fmt: skip
    lead_prompt = told(llms[LEAD], 2)
    assert "The reproduction the controller saw failing" in lead_prompt
    assert "tests/test_regression.py" in lead_prompt
    assert "outside your write scope" in told(llms[LEAD], 3)  # the reproduction is pinned
    profile = build_profile(ref.run_dir, build_table({}))
    assert len(profile.sessions) == 2  # the reproduction and the lead, both the generalist
