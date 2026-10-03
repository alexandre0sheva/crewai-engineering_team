"""Comparing check results with the baseline: "no new failures" instead of "failures".

A project that was already failing before the team touched it cannot be asked to pass
everything; it can be asked not to get worse. A failed detected check (tests, lint, type check,
build) whose failures are all keys the baseline already recorded is recorded as passed with the
known failures listed, so the verdict, the repair loop, and the board all follow without special
cases. A failure that is not in the baseline stays a failure, and only *that* is handed to the
repair agent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from engineering_team.contracts import CheckResult
from engineering_team.modes.baseline_report import BaselineReport, failure_keys

COMPARED = ("test", "lint", "typecheck", "build")
MAX_LISTED = 50


def apply_baseline(
    results: Sequence[CheckResult], directories: Mapping[str, str], baseline: BaselineReport
) -> list[CheckResult]:
    """``results`` with the baseline applied; ``directories`` maps a check id to its project
    directory (the key's directory part)."""

    known = set(baseline.known_failures)
    compared: list[CheckResult] = []
    for result in results:
        if result.status != "failed" or result.kind not in COMPARED or result.source != "detected":
            compared.append(result)
            continue
        keys = failure_keys(result, directories.get(result.id, "."))
        old = [key for key in keys if key in known]
        new = [key for key in keys if key not in known]
        if not old:
            compared.append(result.model_copy(update={"new_failures": new[:MAX_LISTED]}))
        elif not new:
            compared.append(
                result.model_copy(
                    update={
                        "status": "passed",
                        "known_failures": old[:MAX_LISTED],
                        "summary": f"{result.summary}; {len(old)} known failure(s) from the "
                        "baseline, no new ones",
                    }
                )
            )
        else:
            compared.append(
                result.model_copy(
                    update={
                        "known_failures": old[:MAX_LISTED],
                        "new_failures": new[:MAX_LISTED],
                        "summary": f"{result.summary}; {len(new)} new failure(s), "
                        f"{len(old)} known from the baseline",
                    }
                )
            )
    return compared
