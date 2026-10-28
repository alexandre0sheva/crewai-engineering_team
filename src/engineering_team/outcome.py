"""How a run ended, as a process exit code and as one line for the person running it."""

from __future__ import annotations

from engineering_team.pipeline.state import RunResult

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_VERIFICATION_FAILED = 3
EXIT_PARTIAL = 4
EXIT_INTERRUPTED = 130


def exit_code_for(result: RunResult) -> int:
    """The process exit code for how a run ended (see the module docstring)."""

    if result.status == "succeeded":
        return 0
    if result.status == "cancelled":
        return EXIT_INTERRUPTED
    if result.verdict == "failed":
        return EXIT_VERIFICATION_FAILED
    if result.verdict in ("partial", "needs-info"):
        return EXIT_PARTIAL
    return EXIT_FAILURE


def failure_line(result: RunResult, *, resumable: bool) -> str | None:
    """One line saying how a run that did not succeed ended (``None`` for a success)."""

    hint = f" Continue it with: engineering-team resume {result.run_id}" if resumable else ""
    if result.status == "succeeded":
        return None
    if result.status == "cancelled":
        return f"Run {result.run_id} was cancelled.{hint}"
    if result.verdict == "needs-info":
        return f"Run {result.run_id} needs more information: {result.error.rstrip('.')}."
    if result.verdict in ("failed", "partial"):
        return f"Run {result.run_id} is not verified: {result.error.rstrip('.')}.{hint}"
    return f"Engineering team run failed: {result.error.rstrip('.')}.{hint}"
