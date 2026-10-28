"""Scoring a review task's ``findings.json`` against its hidden list of seeded defects.

Standard library only (it runs in a check's process). A task's ``acceptance/defects.json`` lists
each seeded defect: ``{"id", "locations": [{"file", "lines": [first, last]}, ...], "keywords",
"serious"}``; the first location is the defect, the others are where a reviewer may fairly point
instead (the route that passes the bad input on). A finding *matches* a defect when it names one of
its files and either a line within ``SLACK`` lines of that location, or no line and a keyword in its
text. Each finding matches at most one defect (the nearest) and each defect counts once, so recall
and precision cannot be inflated by repeating a finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FINDINGS = "review-out/findings.json"  # where the harness asks ``review --out-dir`` to write
SLACK = 3
SERIOUS = ("critical", "high")
COUNTED = ("critical", "high", "medium")  # precision is over these; low and info are free


@dataclass
class Score:
    findings: int  # findings of medium severity or above
    matched: dict[str, dict[str, Any]] = field(default_factory=dict)  # defect id -> finding
    false_positives: list[dict[str, Any]] = field(default_factory=list)

    def recall(self, defects: int) -> float:
        return len(self.matched) / defects if defects else 1.0

    @property
    def precision(self) -> float:
        hits = self.findings - len(self.false_positives)
        return hits / self.findings if self.findings else 0.0


def load_findings(workspace: Path) -> list[dict[str, Any]]:
    """The findings the team's review wrote; ``AssertionError`` when there are none to read."""

    path = workspace / FINDINGS
    if not path.is_file():
        raise AssertionError(f"no {FINDINGS}: the review wrote no findings file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AssertionError(f"{FINDINGS} is not JSON: {exc}") from None
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        raise AssertionError(f"{FINDINGS} has no findings list")
    return [f for f in findings if isinstance(f, dict)]


def load_defects(task_dir: Path) -> list[dict[str, Any]]:
    data = json.loads((task_dir / "acceptance" / "defects.json").read_text(encoding="utf-8"))
    return list(data)


def _same_file(finding: dict[str, Any], file: str) -> bool:
    named = str(finding.get("file") or "").replace("\\", "/").removeprefix("./")
    return bool(named) and (named == file or named.endswith("/" + file))


def _distance(finding: dict[str, Any], defect: dict[str, Any]) -> int | None:
    """How far the finding is from the defect (0: on it), or ``None`` when it does not match."""

    line = finding.get("line")
    text = f"{finding.get('summary', '')} {finding.get('suggested_fix', '')}".lower()
    found: list[int] = []
    for location in defect["locations"]:
        if not _same_file(finding, location["file"]):
            continue
        first, last = location["lines"]
        if isinstance(line, int) and line > 0:
            gap = max(first - line, line - last, 0)
            if gap <= SLACK:
                found.append(gap)
        elif any(word in text for word in defect["keywords"]):
            found.append(SLACK + 1)
    return min(found) if found else None


def score(findings: list[dict[str, Any]], defects: list[dict[str, Any]]) -> Score:
    counted = [f for f in findings if f.get("severity") in COUNTED]
    result = Score(findings=len(counted))
    for finding in findings:
        candidates = [
            (distance, defect["id"])
            for defect in defects
            if defect["id"] not in result.matched
            and (distance := _distance(finding, defect)) is not None
        ]
        if candidates:
            result.matched[min(candidates)[1]] = finding
        elif finding in counted and not any(_distance(finding, d) is not None for d in defects):
            result.false_positives.append(finding)  # a repeat of a matched defect is not one
    return result


def describe(finding: dict[str, Any]) -> str:
    where = f"{finding.get('file')}:{finding.get('line')}"
    return f"[{finding.get('severity')}] {where} {str(finding.get('summary', ''))[:80]}"
