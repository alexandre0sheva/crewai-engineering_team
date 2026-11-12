"""``findings.sarif``: a review's findings as SARIF 2.1.0, the format GitHub code scanning reads.

Rendered by the controller from ``findings.json`` (itself written from the consolidated findings,
never from an agent's text). Each severity is one rule, so a result's ``ruleId`` says how serious
the reviewers found it; ``error`` is ``critical`` and ``high``, ``warning`` is ``medium``, ``note``
is ``low`` and ``info``. SARIF locates every result in a file, so a project-wide finding (no file)
is not a result: its id is listed in the run's ``withoutLocation`` property and it stays in
``findings.json`` and ``findings.md``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import Severity
from engineering_team.pipeline.review import SEVERITY_ORDER

FINDINGS_SARIF = "findings.sarif"
SARIF_VERSION = "2.1.0"
SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/alexandre0sheva/crewai-engineering_team"
TOOL_NAME = "engineering-team"

LEVELS: dict[Severity, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def rule_id(severity: str) -> str:
    return f"{TOOL_NAME}/review/{severity}"


def _rule(severity: Severity) -> dict[str, Any]:
    return {
        "id": rule_id(severity),
        "name": f"Review finding ({severity})",
        "shortDescription": {"text": f"A {severity} finding from the engineering-team review."},
        "fullDescription": {
            "text": "A claim made by the reviewing agents about the change, to check: "
            "not a proven defect."
        },
        "helpUri": INFORMATION_URI,
        "defaultConfiguration": {"level": LEVELS[severity]},
    }


def _uri(path: str) -> str:
    """The project-relative POSIX path a SARIF artifact location needs."""

    parts = [p for p in PurePosixPath(path.replace("\\", "/")).parts if p not in ("", ".", "/")]
    return "/".join(parts)


def _result(finding: dict[str, Any], uri: str) -> dict[str, Any]:
    text = str(finding["summary"]).strip()
    if finding.get("suggested_fix"):
        text += f"\n\nSuggested fix: {str(finding['suggested_fix']).strip()}"
    physical: dict[str, Any] = {"artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"}}
    line = finding.get("line")
    if isinstance(line, int) and line >= 1:
        physical["region"] = {"startLine": line}
    return {
        "ruleId": rule_id(finding["severity"]),
        "level": LEVELS[finding["severity"]],
        "message": {"text": text},
        "locations": [{"physicalLocation": physical}],
        "properties": {
            "findingId": finding["id"],
            "severity": finding["severity"],
            "sourceRole": finding.get("source_role"),
        },
    }


def to_sarif(data: dict[str, Any], *, version: str) -> dict[str, Any]:
    """The SARIF log of a ``findings.json`` document; most severe first, as in the document."""

    results: list[dict[str, Any]] = []
    without_location: list[str] = []
    for finding in data["findings"]:
        uri = _uri(finding["file"]) if finding.get("file") else ""
        if uri:
            results.append(_result(finding, uri))
        else:
            without_location.append(finding["id"])
    present = {r["properties"]["severity"] for r in results}
    properties: dict[str, Any] = {"runId": data["run_id"], "failOn": data["fail_on"]}
    if without_location:
        properties["withoutLocation"] = without_location
    return {
        "$schema": SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": version,
                        "informationUri": INFORMATION_URI,
                        "rules": [_rule(s) for s in reversed(SEVERITY_ORDER) if s in present],
                    }
                },
                "results": results,
                "properties": properties,
            }
        ],
    }


def write_sarif(data: dict[str, Any], path: Path, *, version: str) -> None:
    atomic_write_json(path, to_sarif(data, version=version))
