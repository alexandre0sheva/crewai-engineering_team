"""What a set of check results means: the verdict, and the brief the repair agent gets."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from engineering_team.contracts import CheckResult, Verdict

MAX_BRIEF_TAIL = 1500


@dataclass(frozen=True)
class Judgement:
    verdict: Verdict
    problems: list[str] = field(default_factory=list)


def judge(results: Sequence[CheckResult], current_revision: str) -> Judgement:
    """``verified`` only when at least one check is required, every required check passed, and
    every result is for the workspace as it is now. A required check that failed makes it
    ``failed``; one that could not run (``unavailable``, ``skipped``), with none failed, makes
    it ``partial``. Results for an older revision are stale evidence: ``failed``."""

    if not results:
        return Judgement("failed", ["no checks ran, so nothing was verified"])
    stale = [r.id for r in results if r.revision != current_revision]
    if stale:
        return Judgement(
            "failed",
            [
                f"results for {', '.join(stale)} are for an older version of the project than "
                "the one now in the workspace"
            ],
        )
    required = [r for r in results if r.required]
    if not required:
        return Judgement("partial", ["no required check exists, so nothing was proven"])
    failed = [
        f"Required check '{r.id}' failed: {r.summary}" for r in required if r.status == "failed"
    ]
    missing = [
        f"Required check '{r.id}' could not run ({r.status}): {r.hint or r.summary}"
        for r in required
        if r.status in ("unavailable", "skipped")
    ]
    if failed:
        return Judgement("failed", [*failed, *missing])
    if missing:
        return Judgement("partial", missing)
    return Judgement("verified")


def failures_for_repair(results: Sequence[CheckResult]) -> str:
    """The structured failures the repair agent is handed: for each failed required check its
    id, command, what it showed, the files it points at, and the end of its log."""

    blocks: list[str] = []
    for result in results:
        if result.status != "failed" or not result.required:
            continue
        lines = [f"- check `{result.id}` ({result.name}) FAILED: {result.summary}"]
        if result.command:
            lines.append(f"  command: {result.command}")
        if result.new_failures:
            lines.append(f"  new failures (fix these): {', '.join(result.new_failures)}")
        if result.known_failures:
            lines.append(
                "  failing before your change, so not yours to fix (leave them): "
                + ", ".join(result.known_failures)
            )
        if result.suspect_files:
            lines.append(f"  suspect files: {', '.join(result.suspect_files)}")
        if result.hint:
            lines.append(f"  hint: {result.hint}")
        if result.log_tail:
            tail = result.log_tail[-MAX_BRIEF_TAIL:]
            lines.append("  output (end):")
            lines.extend(f"    {row}" for row in tail.splitlines())
        blocks.append("\n".join(lines))
    advisory = [r.id for r in results if r.status == "failed" and not r.required]
    if advisory:
        blocks.append(
            f"Also failing, but not required (do not spend effort here unless it is cheap): "
            f"{', '.join(advisory)}."
        )
    return "\n\n".join(blocks)
