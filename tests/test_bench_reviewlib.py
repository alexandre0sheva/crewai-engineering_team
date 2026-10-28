"""Scoring a review's findings against the seeded defects: recall, precision, no double counting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_team.bench.reviewlib import FINDINGS, load_findings, score

DEFECTS = [
    {"id": "sqli", "serious": True, "keywords": ["injection"],
     "locations": [{"file": "app/db.py", "lines": [10, 12]},
                   {"file": "app/web.py", "lines": [5, 5]}]},
    {"id": "leak", "serious": False, "keywords": ["close"],
     "locations": [{"file": "app/files.py", "lines": [30, 30]}]},
]  # fmt: skip


def finding(file: str, line: int | None, severity: str = "high", summary: str = "x") -> dict:
    return {"file": file, "line": line, "severity": severity, "summary": summary}


def test_a_finding_matches_near_a_defect_or_at_another_of_its_locations() -> None:
    result = score([finding("app/db.py", 14), finding("./app/files.py", 33)], DEFECTS)

    assert set(result.matched) == {"sqli", "leak"}
    assert score([finding("src/app/web.py", 5)], DEFECTS).matched.keys() == {"sqli"}
    assert not score([finding("app/db.py", 16)], DEFECTS).matched


def test_without_a_line_the_wording_decides() -> None:
    assert score([finding("app/db.py", None, summary="SQL Injection")], DEFECTS).matched
    assert not score([finding("app/db.py", None, summary="naming")], DEFECTS).matched


def test_repeats_count_once_and_are_not_false_positives() -> None:
    result = score([finding("app/db.py", 10), finding("app/db.py", 11)], DEFECTS)

    assert result.recall(len(DEFECTS)) == 0.5
    assert result.precision == 1.0


def test_precision_ignores_low_and_info_findings() -> None:
    found = [finding("app/db.py", 10), finding("x.py", 1, "low"), finding("y.py", 1, "medium")]

    result = score(found, DEFECTS)

    assert result.findings == 2 and result.precision == 0.5
    assert [f["file"] for f in result.false_positives] == ["y.py"]


def test_missing_or_malformed_findings_fail_the_check(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="no review-out/findings.json"):
        load_findings(tmp_path)
    (tmp_path / FINDINGS).parent.mkdir()
    (tmp_path / FINDINGS).write_text(json.dumps({"findings": "none"}))
    with pytest.raises(AssertionError, match="no findings list"):
        load_findings(tmp_path)
