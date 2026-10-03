"""The review stage: findings validated and merged, reviewers in parallel lanes, serious
findings repaired and verified again, and the optional stages skipped when they should be."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from pipeline_fakes import FakeRunner
from test_pipeline_flow import only_run, project, run_dir, start, use_runner

from engineering_team.board.store import BoardStore
from engineering_team.contracts import Finding, ReviewReport
from engineering_team.pipeline.review import (
    Reviewed,
    blocking,
    clean_finding,
    consolidate,
    dedupe,
    render_review,
    repair_brief,
)
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.events import read_events


def finding(summary: str, severity: str = "high", **fields: object) -> Finding:
    return Finding(severity=severity, summary=summary, **fields)  # type: ignore[arg-type]


def report(*findings: Finding, summary: str = "Reviewed.") -> ReviewReport:
    return ReviewReport(summary=summary, findings=list(findings))


def events(type_: str) -> list[object]:
    return [e for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == type_]


def state() -> PipelineState:
    loaded = PipelineState.load(run_dir(only_run()))
    assert loaded is not None
    return loaded


def statuses() -> dict[str, str]:
    return {record.name: record.status for record in only_run().stages}


def review_text() -> str:
    return (project() / "docs" / "review.md").read_text(encoding="utf-8")


# -- findings: validation, merging, thresholds ---------------------------------------------------


def test_a_finding_is_cleaned_and_its_role_is_set_by_the_controller() -> None:
    raw = finding(
        "  Password stored\nin plain   text ",
        id="X-9",
        file="./src\\db.py",
        line=12,
        suggested_fix=" hash it ",
        source_role="someone_else",
    )

    kept, why = clean_finding(raw, "security_engineer")

    assert why == "" and kept is not None
    assert (kept.id, kept.file, kept.line) == ("", "src/db.py", 12)
    assert kept.summary == "Password stored in plain text" and kept.suggested_fix == "hash it"
    assert kept.source_role == "security_engineer"  # not what the agent claimed


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (finding("  "), "no summary"),
        (finding("Leaks", file="/etc/passwd"), "not inside the project"),
        (finding("Leaks", file="../../secret"), "not inside the project"),
    ],
)
def test_findings_without_text_or_outside_the_project_are_dropped(raw: Finding, why: str) -> None:
    kept, reason = clean_finding(raw, "code_reviewer")

    assert kept is None and why in reason


def test_a_line_without_a_file_is_ignored() -> None:
    kept, _ = clean_finding(finding("Global issue", line=5), "code_reviewer")

    assert kept is not None and kept.line is None and kept.file is None


def test_the_same_finding_from_two_reviewers_is_one_finding_naming_both() -> None:
    a = finding("SQL injection in the query builder", "medium", file="app/db.py", line=40)
    b = finding(
        "The query builder allows SQL injection", "critical", file="app/db.py", line=42,
        suggested_fix="Use parameters.",
    )  # fmt: skip
    a = a.model_copy(update={"source_role": "code_reviewer"})
    b = b.model_copy(update={"source_role": "security_engineer"})

    (merged,) = dedupe([a, b])

    assert merged.severity == "critical" and merged.line == 40
    assert merged.source_role == "code_reviewer, security_engineer"
    assert merged.suggested_fix == "Use parameters."


def test_different_files_far_apart_lines_or_different_problems_stay_separate() -> None:
    base = finding("Missing input validation", file="a.py", line=10)
    others = [
        finding("Missing input validation", file="b.py", line=10),
        finding("Missing input validation", file="a.py", line=200),
        finding("Unused import left behind", file="a.py", line=10),
    ]

    assert len(dedupe([base, *others])) == 4


def test_consolidation_numbers_findings_most_serious_first_whatever_the_reviewer_order() -> None:
    low = finding("Typo in message", "low", file="z.py", line=1)
    crit = finding("Data loss on save", "critical", file="a.py", line=3)
    high = finding("No error handling", "high", file="m.py", line=9)
    forward = [
        Reviewed("code_reviewer", report(low, high)),
        Reviewed("security_engineer", report(crit)),
    ]

    first = consolidate(forward)
    second = consolidate(list(reversed(forward)))

    assert [(f.id, f.severity) for f in first] == [
        ("F-1", "critical"),
        ("F-2", "high"),
        ("F-3", "low"),
    ]
    assert [(f.id, f.summary) for f in first] == [(f.id, f.summary) for f in second]


def test_consolidation_reports_what_it_dropped_and_skips_reviewers_without_a_report() -> None:
    reviews = [
        Reviewed("code_reviewer", report(finding("Real issue", file="a.py"), finding(""))),
        Reviewed("security_engineer", error="model unavailable"),
    ]

    found = consolidate(reviews)

    assert [f.summary for f in found] == ["Real issue"]
    assert reviews[0].dropped == ["a finding with no summary"]


def test_the_threshold_picks_findings_at_or_above_it() -> None:
    items = [finding("a", s) for s in ("info", "low", "medium", "high", "critical")]

    assert [f.severity for f in blocking(items, "high")] == ["high", "critical"]
    assert [f.severity for f in blocking(items, "medium")] == ["medium", "high", "critical"]
    assert [f.severity for f in blocking(items, "critical")] == ["critical"]


def test_the_rendered_review_and_repair_brief_name_ids_places_and_outcomes() -> None:
    reviews = [
        Reviewed("code_reviewer", report(finding("Crash on empty input", file="a.py", line=7))),
        Reviewed("security_engineer", error="timed out"),
    ]
    found = consolidate(reviews)

    text = render_review(found, reviews, fail_on="high", outcome="Sent to repair.")

    assert "**F-1** [high] (sent to repair) `a.py:7`: Crash on empty input" in text
    assert "security_engineer: did not report (timed out)" in text and "Sent to repair." in text
    assert "- F-1 [high] a.py:7: Crash on empty input" in repair_brief(found)
    assert "No findings." in render_review([], reviews, fail_on="high")


# -- the stage in a run -------------------------------------------------------------------------


def test_two_reviewers_run_at_the_same_time_in_their_own_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2, timeout=15)  # both must be inside the stage at once to pass

    def meet(request: StageRequest) -> None:
        if request.stage.name == "review":
            barrier.wait()

    use_runner(monkeypatch, FakeRunner(on_call=meet))

    assert start() == 0

    started = [e for e in events("lane.started") if e.stage == "review"]  # type: ignore[attr-defined]
    assert sorted((e.lane, e.data["unit"]) for e in started) == [  # type: ignore[attr-defined]
        (1, "code_reviewer"),
        (2, "security_engineer"),
    ]
    assert all(e.data["kind"] == "job" for e in started)  # type: ignore[attr-defined]
    assert statuses()["review"] == "succeeded" and "No findings." in review_text()


def test_serious_findings_are_repaired_and_the_project_is_verified_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fix(request: StageRequest) -> None:
        if request.findings:
            request.ctx.workspace.write_file("src/fixed.py", "VALUE = 1\n")

    reviews = {
        "code_reviewer": report(
            finding("Crash when the file is empty", "high", file="src/wp-1.py", line=2),
            finding("Rename the helper", "low", file="src/wp-2.py", line=1),
        ),
        "security_engineer": report(
            finding("Empty file makes the program crash", "critical", file="src/wp-1.py", line=3)
        ),
    }
    runner = use_runner(monkeypatch, FakeRunner(reviews=reviews, on_call=fix))

    assert start() == 0

    (repair,) = [r for r in runner.requests if r.findings]
    assert repair.stage.name == "verify" and repair.teammate == "debugger" and not repair.failures
    assert "F-1 [critical]" in repair.findings and "Rename the helper" not in repair.findings
    found = state().findings
    assert [(f.id, f.severity) for f in found] == [("F-1", "critical"), ("F-2", "low")]
    assert found[0].source_role == "code_reviewer, security_engineer"  # merged
    text = review_text()
    assert "(sent to repair)" in text and "Sent to repair and verified again" in text
    assert only_run().verdict == "verified"
    record = state().verification
    assert record.rounds == 1 and "review finding(s) F-1" in record.repair_log[0]
    types = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert "review.findings" in types and types.count("verify.verdict") >= 2  # verified again
    board = BoardStore(run_dir(only_run()))
    (card,) = board.cards(kind="repair")
    assert card.assignee == "debugger" and card.status == "done"


def test_findings_below_the_threshold_are_only_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    reviews = {"code_reviewer": report(finding("Naming nit", "medium", file="a.py", line=1))}
    runner = use_runner(monkeypatch, FakeRunner(reviews=reviews))

    assert start() == 0

    assert not [r for r in runner.requests if r.findings]
    assert state().verification.rounds == 0 and "(sent to repair)" not in review_text()
    assert "Naming nit" in review_text()


def test_the_threshold_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Path.cwd() / "engineering-team.toml"
    config.write_text('[review]\nfail_on = "medium"\n', encoding="utf-8")
    reviews = {"code_reviewer": report(finding("Naming nit", "medium", file="a.py", line=1))}
    runner = use_runner(monkeypatch, FakeRunner(reviews=reviews))

    assert start("--config", str(config)) == 0

    assert len([r for r in runner.requests if r.findings]) == 1


def test_without_a_repair_round_left_the_findings_are_reported_not_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS", "0")
    reviews = {"code_reviewer": report(finding("Crash on empty input", "high", file="a.py"))}
    runner = use_runner(monkeypatch, FakeRunner(reviews=reviews))

    assert start() == 0

    assert not [r for r in runner.requests if r.findings]
    assert "No repair round was left" in review_text()
    assert [e.data["findings"] for e in events("verify.repair_skipped")] == [["F-1"]]  # type: ignore[attr-defined]


def test_a_repair_that_changes_nothing_fails_its_card_but_not_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviews = {"code_reviewer": report(finding("Crash on empty input", "high", file="a.py"))}
    use_runner(monkeypatch, FakeRunner(reviews=reviews))  # the repair agent does nothing

    assert start() == 0

    board = BoardStore(run_dir(only_run()))
    (card,) = board.cards(kind="repair")
    assert card.status == "failed"
    assert "changed nothing" in state().verification.repair_log[0]


def test_an_invalid_finding_is_dropped_and_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    reviews = {
        "code_reviewer": report(
            finding("Leaks a secret", "high", file="/etc/passwd"),
            finding("Real problem", "low", file="a.py"),
        )
    }
    use_runner(monkeypatch, FakeRunner(reviews=reviews))

    assert start() == 0

    assert [f.summary for f in state().findings] == ["Real problem"]
    assert (
        "dropped: 'Leaks a secret': the file '/etc/passwd' is not inside the project"
        in review_text()
    )


def test_one_reviewer_failing_does_not_fail_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = use_runner(monkeypatch, FakeRunner(fail={"review": 1}))

    assert start() == 0

    assert statuses()["review"] == "succeeded" and len(runner.calls) > 0
    assert "did not report (RuntimeError: scripted failure in review" in review_text()


def test_every_reviewer_failing_fails_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    use_runner(monkeypatch, FakeRunner(fail={"review": 2}))

    assert start() == 1

    assert statuses()["review"] == "failed"
    assert next(r for r in only_run().stages if r.name == "review").detail.startswith(
        "StageError: No reviewer reported"
    )


# -- the optional stages ------------------------------------------------------------------------

OPTIONAL = ("review", "devops", "docs")


@pytest.mark.parametrize(
    ("variable", "value", "reason"),
    [
        ("ENGINEERING_TEAM_PROFILE", "minimal", "minimal_team"),
        ("ENGINEERING_RUN_PROFILE", "smoke", "minimal_team"),
    ],
)
def test_a_minimal_team_skips_review_devops_and_docs(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, reason: str
) -> None:
    monkeypatch.setenv(variable, value)
    runner = use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    assert not {stage for stage, _ in runner.calls} & set(OPTIONAL)
    skipped = {r.name: r.detail for r in only_run().stages if r.status == "skipped"}
    assert set(skipped) == set(OPTIONAL) and all(reason in d for d in skipped.values())
    assert not (project() / "docs" / "review.md").exists()
    assert not (project() / "docs" / "devops.md").exists()


def test_disabling_a_teammate_skips_its_stage_and_one_reviewer_still_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Path.cwd() / "engineering-team.toml"
    config.write_text(
        "[team.devops_engineer]\nenabled = false\n\n[team.security_engineer]\nenabled = false\n",
        encoding="utf-8",
    )
    runner = use_runner(monkeypatch, FakeRunner())

    assert start("--config", str(config)) == 0

    assert statuses()["devops"] == "skipped" and statuses()["review"] == "succeeded"
    assert [r.teammate for r in runner.requests if r.stage.name == "review"] == ["code_reviewer"]
    assert "no_enabled_teammate" in next(r for r in only_run().stages if r.name == "devops").detail


def test_disabling_both_reviewers_skips_the_review(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Path.cwd() / "engineering-team.toml"
    config.write_text(
        "[team.code_reviewer]\nenabled = false\n\n[team.security_engineer]\nenabled = false\n",
        encoding="utf-8",
    )
    use_runner(monkeypatch, FakeRunner())

    assert start("--config", str(config)) == 0

    assert statuses()["review"] == "skipped" and statuses()["docs"] == "succeeded"


def test_the_final_re_verify_covers_what_devops_and_docs_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())

    assert start() == 0

    types = [e.type for e in read_events(run_dir(only_run()) / "events.jsonl")]
    assert types.count("check.started") == 2  # once in verify, once after the last stage
    assert only_run().verdict == "verified"
