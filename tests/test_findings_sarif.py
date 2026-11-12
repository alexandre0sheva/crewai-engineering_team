"""``findings.sarif``: the SARIF 2.1.0 rendering of a review's findings (for code scanning)."""

from __future__ import annotations

from typing import Any

from engineering_team.modes.findings_sarif import SARIF_VERSION, to_sarif


def finding(fid: str, severity: str, summary: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "summary": summary,
        "file": None,
        "line": None,
        "suggested_fix": None,
        "source_role": "code_reviewer",
        **fields,
    }


def document(*findings: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "review", "run_id": "r-1", "fail_on": "high", "findings": list(findings)}


def test_a_finding_becomes_a_result_with_a_level_and_a_location() -> None:
    sarif = to_sarif(
        document(
            finding(
                "F-1",
                "high",
                "add drops earlier notes",
                file="app/store.py",
                line=2,
                suggested_fix="Append instead of replacing.",
            )  # fmt: skip
        ),
        version="1.2.3",
    )

    assert sarif["version"] == SARIF_VERSION == "2.1.0"
    assert sarif["$schema"].endswith("sarif-2.1.0.json")
    (run,) = sarif["runs"]
    assert run["tool"]["driver"]["name"] == "engineering-team"
    assert run["tool"]["driver"]["version"] == "1.2.3"
    (result,) = run["results"]
    assert result["ruleId"] == "engineering-team/review/high" and result["level"] == "error"
    assert result["message"]["text"] == (
        "add drops earlier notes\n\nSuggested fix: Append instead of replacing."
    )
    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"] == {"uri": "app/store.py", "uriBaseId": "%SRCROOT%"}
    assert physical["region"] == {"startLine": 2}
    assert result["properties"] == {
        "findingId": "F-1",
        "severity": "high",
        "sourceRole": "code_reviewer",
    }
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == [result["ruleId"]]


def test_severities_map_to_sarif_levels() -> None:
    sarif = to_sarif(
        document(
            *(
                finding(f"F-{i}", severity, f"{severity} problem", file="a.py")
                for i, severity in enumerate(["critical", "high", "medium", "low", "info"], 1)
            )
        ),
        version="0",
    )

    levels = {r["properties"]["severity"]: r["level"] for r in sarif["runs"][0]["results"]}
    assert levels == {
        "critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note",
    }  # fmt: skip
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert [r["id"].rsplit("/", 1)[1] for r in rules] == [
        "critical",
        "high",
        "medium",
        "low",
        "info",
    ]
    assert all(r["defaultConfiguration"]["level"] and r["shortDescription"]["text"] for r in rules)


def test_a_path_is_posix_relative_and_a_missing_or_bad_line_is_left_out() -> None:
    sarif = to_sarif(
        document(
            finding("F-1", "low", "windows path", file="app\\store.py", line=0),
            finding("F-2", "low", "dot slash", file="./app/main.py", line=7),
        ),
        version="0",
    )

    first, second = sarif["runs"][0]["results"]
    assert first["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "app/store.py"
    assert "region" not in first["locations"][0]["physicalLocation"]
    assert second["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "app/main.py"


def test_a_project_wide_finding_has_no_place_in_the_file_so_it_is_not_a_result() -> None:
    sarif = to_sarif(
        document(
            finding("F-1", "high", "no migration plan"),
            finding("F-2", "low", "typo", file="README.md", line=3),
        ),
        version="0",
    )

    run = sarif["runs"][0]
    assert [r["properties"]["findingId"] for r in run["results"]] == ["F-2"]
    # What is not shown as a result is still counted, so a reader knows the file is incomplete.
    assert run["properties"] == {"runId": "r-1", "failOn": "high", "withoutLocation": ["F-1"]}


def test_a_clean_review_is_a_valid_run_with_no_results() -> None:
    sarif = to_sarif(document(), version="0")

    run = sarif["runs"][0]
    assert run["results"] == [] and run["tool"]["driver"]["rules"] == []
    assert "withoutLocation" not in run["properties"]
