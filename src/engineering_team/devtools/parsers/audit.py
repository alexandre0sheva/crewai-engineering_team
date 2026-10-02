"""Dependency audit reports: pip-audit, npm audit, cargo audit."""

from __future__ import annotations

import json
from typing import Any

from engineering_team.devtools.models import Vulnerability
from engineering_team.devtools.parsers.common import excerpt

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "medium": 2, "low": 3, "info": 4}


class AuditError(ValueError):
    """The output is not the JSON the audit tool writes."""


def _load(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AuditError(f"{what}: not valid JSON ({exc})") from exc


def parse_pip_audit(text: str) -> list[Vulnerability]:
    """``pip-audit -f json``."""

    data = _load(text, "pip-audit")
    dependencies = data.get("dependencies") if isinstance(data, dict) else data
    if not isinstance(dependencies, list):
        raise AuditError("pip-audit: no 'dependencies'; use -f json")
    found = []
    for dependency in dependencies:
        for vuln in dependency.get("vulns", []):
            fixes = vuln.get("fix_versions") or []
            found.append(
                Vulnerability(
                    package=str(dependency.get("name", "")),
                    version=str(dependency.get("version", "")),
                    id=str(vuln.get("id", "")),
                    fixed_in=", ".join(fixes),
                    title=excerpt(str(vuln.get("description", "")), 160),
                )
            )
    return found


def parse_npm_audit(text: str) -> list[Vulnerability]:
    """``npm audit --json`` (npm 7+ ``vulnerabilities``; npm 6 ``advisories``)."""

    data = _load(text, "npm audit")
    if not isinstance(data, dict):
        raise AuditError("npm audit: expected a JSON object")
    if "error" in data and "vulnerabilities" not in data:
        raise AuditError(f"npm audit: {excerpt(str(data['error']), 200)}")
    found = []
    if "vulnerabilities" in data:
        for name, entry in data["vulnerabilities"].items():
            via = [item for item in entry.get("via", []) if isinstance(item, dict)]
            fix = entry.get("fixAvailable")
            found.append(
                Vulnerability(
                    package=name,
                    version=str(entry.get("range", "")),
                    id=str(via[0].get("url", "")).rsplit("/", 1)[-1] if via else "",
                    severity=str(entry.get("severity", "unknown")),
                    fixed_in=str(fix.get("version", "")) if isinstance(fix, dict) else "",
                    title=excerpt(str(via[0].get("title", "")), 160) if via else "",
                )
            )
    elif "advisories" in data:
        for advisory in data["advisories"].values():
            found.append(
                Vulnerability(
                    package=str(advisory.get("module_name", "")),
                    version=str(advisory.get("vulnerable_versions", "")),
                    id=str(advisory.get("github_advisory_id") or advisory.get("id", "")),
                    severity=str(advisory.get("severity", "unknown")),
                    fixed_in=str(advisory.get("patched_versions", "")),
                    title=excerpt(str(advisory.get("title", "")), 160),
                )
            )
    else:
        raise AuditError("npm audit: no 'vulnerabilities' or 'advisories'; use --json")
    return found


def parse_cargo_audit(text: str) -> list[Vulnerability]:
    """``cargo audit --json``."""

    data = _load(text, "cargo audit")
    try:
        entries = data["vulnerabilities"]["list"]
    except (KeyError, TypeError) as exc:
        raise AuditError("cargo audit: no 'vulnerabilities.list'; use --json") from exc
    return [
        Vulnerability(
            package=str(entry.get("package", {}).get("name", "")),
            version=str(entry.get("package", {}).get("version", "")),
            id=str(entry.get("advisory", {}).get("id", "")),
            severity=str(entry.get("advisory", {}).get("severity") or "unknown"),
            fixed_in=", ".join(entry.get("versions", {}).get("patched", [])),
            title=excerpt(str(entry.get("advisory", {}).get("title", "")), 160),
        )
        for entry in entries
    ]


def sort_and_cap(items: list[Vulnerability], limit: int) -> tuple[list[Vulnerability], int]:
    ordered = sorted(
        items, key=lambda v: (SEVERITY_ORDER.get(v.severity.lower(), 5), v.package, v.id)
    )
    return ordered[:limit], max(len(ordered) - limit, 0)
