"""What the results mean (verdict), which criteria they prove, and the controller's report."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.contracts import (
    AcceptanceCriterion,
    CheckKind,
    CheckResult,
    CheckSpec,
    CheckStatus,
    CriterionCoverage,
    Spec,
    VerificationRecord,
)
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.verification.criteria import map_criteria
from engineering_team.verification.report import render_report
from engineering_team.verification.verdict import failures_for_repair, judge

REV = "a" * 32


def result(
    check_id: str,
    status: CheckStatus = "passed",
    *,
    required: bool = True,
    kind: CheckKind = "test",
    revision: str | None = REV,
    **fields: object,
) -> CheckResult:
    return CheckResult(
        id=check_id, status=status, required=required, kind=kind, revision=revision,
        name=check_id.capitalize(), summary=str(fields.pop("summary", status)), **fields,  # type: ignore[arg-type]
    )  # fmt: skip


# -- verdict ---------------------------------------------------------------------------------


def test_every_required_check_passing_on_the_current_revision_is_verified() -> None:
    judgement = judge([result("tests"), result("lint", "failed", required=False)], REV)

    assert judgement.verdict == "verified" and judgement.problems == []


@pytest.mark.parametrize(
    ("results", "verdict", "mention"),
    [
        ([], "failed", "no checks ran"),
        ([result("tests", "failed", summary="2 failed")], "failed", "tests"),
        ([result("tests"), result("build", "failed")], "failed", "build"),
        ([result("tests"), result("build", "unavailable")], "partial", "build"),
        ([result("tests"), result("smoke", "skipped")], "partial", "smoke"),
        ([result("tests", "failed"), result("build", "unavailable")], "failed", "tests"),
        ([result("lint", required=False)], "partial", "no required check"),
        ([result("tests", revision="b" * 32)], "failed", "older version"),
        ([result("tests", revision=None)], "failed", "older version"),
    ],
)
def test_anything_short_of_a_clean_pass_names_what_is_missing(
    results: list[CheckResult], verdict: str, mention: str
) -> None:
    judgement = judge(results, REV)

    assert judgement.verdict == verdict
    assert mention in " ".join(judgement.problems)


def test_the_repair_brief_lists_each_failed_required_check_with_its_evidence() -> None:
    failing = result(
        "tests", "failed", summary="1 failed", command="pytest -q", suspect_files=["calc.py"],
        log_tail="calc.py:2: assert 3 == 4",
    )  # fmt: skip

    brief = failures_for_repair([failing, result("lint", "failed", required=False), result("b")])

    assert "tests" in brief and "pytest -q" in brief and "calc.py" in brief
    assert "assert 3 == 4" in brief
    assert "lint" in brief and "not required" in brief  # advisory failures are mentioned, briefly
    assert "- b" not in brief  # passing checks are not


# -- criteria --------------------------------------------------------------------------------

SPEC = Spec(
    title="Notes",
    criteria=[
        AcceptanceCriterion(id="AC-1", text="add stores a note"),
        AcceptanceCriterion(id="AC-2", text="list prints notes"),
        AcceptanceCriterion(id="AC-3", text="notes persist"),
        AcceptanceCriterion(id="AC-10", text="export works"),
    ],
)


def coverage_by_id(coverage: list[CriterionCoverage]) -> dict[str, CriterionCoverage]:
    return {c.id: c for c in coverage}


def test_a_criterion_is_verified_only_by_a_passing_check_that_maps_to_it(
    tmp_path: Path,
) -> None:
    specs = [
        CheckSpec(id="e2e", name="e2e", criteria_ids=["AC-1"], source="user"),
        CheckSpec(id="e2e2", name="e2e2", criteria_ids=["AC-2"], source="user"),
    ]
    results = [result("e2e"), result("e2e2", "failed")]

    coverage = coverage_by_id(map_criteria(ProjectWorkspace.create(tmp_path), SPEC, specs, results))

    assert coverage["AC-1"].status == "verified" and coverage["AC-1"].checks == ["e2e"]
    assert coverage["AC-2"].status == "unverified" and "e2e2" in coverage["AC-2"].note
    assert coverage["AC-3"].status == "unverified" and coverage["AC-3"].checks == []
    assert coverage["AC-1"].text == "add stores a note"


def test_a_passing_suite_that_mentions_a_criterion_is_only_a_hint(tmp_path: Path) -> None:
    ws = ProjectWorkspace.create(tmp_path)
    ws.write_file("tests/test_notes.py", "def test_add():  # AC-1\n    assert True\n")
    ws.write_file("src/notes.py", "# AC-3 lives here\n")  # not a test file
    ws.write_file("tests/test_export.py", "def test_x():  # AC-10\n    assert True\n")

    passing = map_criteria(ws, SPEC, [], [result("tests")])
    failing = map_criteria(ws, SPEC, [], [result("tests", "failed")])

    found = coverage_by_id(passing)
    assert found["AC-1"].status == "referenced" and "tests/test_notes.py" in found["AC-1"].note
    assert found["AC-3"].status == "unverified"
    assert found["AC-10"].status == "referenced" and "tests/test_export.py" in found["AC-10"].note
    assert found["AC-2"].status == "unverified"  # AC-1 and AC-10 do not count for AC-2
    assert {c.status for c in failing} == {"unverified"}  # a failing suite proves nothing


def test_no_spec_means_no_criteria(tmp_path: Path) -> None:
    assert map_criteria(ProjectWorkspace.create(tmp_path), None, [], []) == []


# -- the report ------------------------------------------------------------------------------


def record(**fields: object) -> VerificationRecord:
    return VerificationRecord(**{"rounds": 1, "revision": REV, **fields})  # type: ignore[arg-type]


def test_the_report_leads_with_the_verdict_and_lists_every_check() -> None:
    results = [
        result("tests", command="pytest -q", exit_code=0, summary="3 passed, 0 failed, 0 skipped"),
        result("lint", "failed", required=False, command="ruff check .", exit_code=1,
               summary="2 error(s), 0 warning(s)", log_tail="calc.py:1:1 E501"),
        result("smoke", "unavailable", kind="smoke", hint="Install Node.js."),
    ]  # fmt: skip
    judgement = judge(results, REV)

    text = render_report(record(), results, judgement, max_rounds=3)

    assert text.startswith("# Verification")
    assert "controller" in text.split("## Independent checks")[0].lower()
    assert "## Independent checks" in text and "`pytest -q`" in text
    assert "3 passed, 0 failed, 0 skipped" in text and "PASSED" in text and "UNAVAILABLE" in text
    assert "calc.py:1:1 E501" in text and "Install Node.js." in text
    assert "Repair rounds used: 1 of 3" in text


def test_the_report_says_plainly_when_the_project_is_not_verified() -> None:
    results = [result("tests", "failed", summary="1 failed")]

    text = render_report(record(), results, judge(results, REV), max_rounds=3)

    assert "**Verdict: FAILED**" in text and "Required check 'tests' failed" in text


def test_unverified_criteria_are_listed_for_a_human() -> None:
    coverage = [
        CriterionCoverage(id="AC-1", text="add works", status="verified", checks=["e2e"]),
        CriterionCoverage(id="AC-2", text="list | prints", status="referenced",
                          note="mentioned in tests/test_x.py"),
        CriterionCoverage(id="AC-3", text="persist", status="unverified"),
    ]  # fmt: skip
    results = [result("tests")]

    text = render_report(record(coverage=coverage), results, judge(results, REV), max_rounds=3)

    assert "## Acceptance criteria" in text
    assert "| AC-1 | add works | verified | e2e |" in text
    assert "list \\| prints" in text  # a pipe in agent-written text cannot break the table
    manual = text.split("### Manual / unverified")[1]
    assert "AC-2" in manual and "AC-3" in manual and "AC-1" not in manual


def test_the_report_is_reproducible_and_carries_nothing_run_specific() -> None:
    results = [result("tests", duration=1.23, log_path=".engineering-team/runs/20260101-x/a.log")]
    judgement = judge(results, REV)

    one = render_report(record(), results, judgement, max_rounds=3)
    other = render_report(record(), results, judgement, max_rounds=3)

    assert one == other
    assert "20260101" not in one and "1.23" not in one


def test_a_log_containing_a_code_fence_cannot_break_out_of_the_report() -> None:
    results = [result("tests", "failed", log_tail="before\n```\n# injected heading\n```\nafter")]

    text = render_report(record(), results, judge(results, REV), max_rounds=3)

    fence = next(line for line in text.splitlines() if line.startswith("````"))
    assert text.count(fence) == 2  # opened and closed by a longer fence
