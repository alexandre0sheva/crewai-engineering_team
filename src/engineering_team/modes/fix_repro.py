"""The red gate of ``fix`` mode: the controller runs the reproduction and decides what it means.

An agent can claim anything about a bug. What the controller accepts is its own run: the
reproduction must *fail* on the tree before the fix (``red``), for a real reason (a test that
failed, not a command that could not start), and the very same command must pass after it
(``green``, in the verification stage). The files of the reproduction are pinned when they are
seen failing, so editing the test to make it pass is noticed rather than rewarded.

When no attempt reproduces the bug the stage raises :class:`NeedsInfo` with concrete questions
(the run ends ``needs-info``, exit 4) unless the person allowed fixing without a reproduction.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from engineering_team.contracts import CheckResult, CheckSpec, Contract
from engineering_team.modes.fix_contracts import FixRecord, Repro, ReproRun
from engineering_team.modes.fix_input import FixInput, read_fix_input
from engineering_team.modes.needs_info import NeedsInfo
from engineering_team.modes.trace import parse_trace, suspect_files
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.tools.workspace import WorkspaceError
from engineering_team.verification.revision import verification_revision
from engineering_team.verification.verifier import Verifier

REPRO_ID = "repro"
USER_REPRO_ID = "repro-user"
NOT_STARTED = (126, 127)
PYTEST_NOT_A_FAILURE = {
    2: "pytest was interrupted or could not collect the tests (an import or syntax error?)",
    4: "pytest was called wrongly (usage error)",
    5: "pytest found no test to run",
}
if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

MAX_NOTE = 1200
DEFAULT_QUESTIONS = (
    "Which exact command, request, or click sequence shows the bug, and on which input?",
    "What did you expect to happen, and what happened instead (the exact error text)?",
    "Which versions or settings does it depend on (runtime, configuration, data)?",
)


def not_a_failure(argv: Sequence[str], result: CheckResult) -> str | None:
    """Why ``result`` is not a reproduction that failed; ``None`` when it is one."""

    if result.status == "passed":
        return "the reproduction passed, so it does not show the bug"
    if result.status != "failed":
        return result.hint or "the controller could not run the reproduction command"
    if "timed out" in result.summary:
        return f"the reproduction {result.summary}, which shows nothing"
    code = result.exit_code
    if code in NOT_STARTED:
        return f"the command could not be started (exit {code}): check its name and path"
    if code in PYTEST_NOT_A_FAILURE and any(
        Path(a).name in ("pytest", "py.test") or a == "pytest" for a in argv
    ):
        return f"{PYTEST_NOT_A_FAILURE[code]} (exit {code}), which is not a failing test"
    return None


# -- running the reproduction ----------------------------------------------------------------


def _spec(ctx: RunContext, check_id: str, command: str, source: str) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        name="Reproduction" if check_id == REPRO_ID else "Reproduction (your command)",
        argv=shlex.split(command),
        required=True,
        timeout=float(ctx.settings.fix.repro_timeout),
        kind="custom",
        source=source,  # type: ignore[arg-type]  # "plan" (allowlisted) or "user"
    )


def _run(ctx: RunContext, check_id: str, command: str, source: str, label: str) -> CheckResult:
    return Verifier(ctx).run([_spec(ctx, check_id, command, source)], label=label)[0]


def _record(
    result: CheckResult, phase: Literal["red", "green"], command: str, attempt: int
) -> ReproRun:
    status = result.status if result.status in ("failed", "passed") else "unavailable"
    return ReproRun(
        phase=phase,
        command=result.command or command,
        status=status,
        exit_code=result.exit_code,
        summary=result.summary,
        log_path=result.log_path,
        revision=result.revision,
        attempt=attempt,
    )


def _digest(root: Path, relative: str) -> str:
    try:
        return hashlib.sha256((root / relative).read_bytes()).hexdigest()
    except OSError:
        return ""


def _problem_with(ctx: RunContext, repro: Repro) -> str | None:
    """What is wrong with the reproduction before it is even run, if anything."""

    if not repro.command.strip():
        return "no command was given to run the reproduction"
    if not repro.files:
        return "no reproduction file was named (a failing test or script that stays in the project)"
    missing = []
    for name in repro.files:
        try:
            ctx.workspace.resolve(name, must_exist=True)
        except WorkspaceError:
            missing.append(name)
    return f"these reproduction files do not exist: {', '.join(missing)}" if missing else None


def _attempt(ctx: RunContext, repro: Repro, number: int) -> tuple[ReproRun | None, str | None]:
    """Run the agent's reproduction once. Returns its red record and, when it is not red, why."""

    problem = _problem_with(ctx, repro)
    if problem is not None:
        return None, problem
    try:
        argv = shlex.split(repro.command)
    except ValueError as exc:
        return None, f"the command is not a valid command line ({exc})"
    try:
        result = _run(ctx, REPRO_ID, repro.command, "plan", f"repro-red-{number}")
    except WorkspaceError as exc:
        return None, f"the controller may not run this command: {exc}"
    reason = not_a_failure(argv, result)
    run = _record(result, "red", repro.command, number)
    if reason is not None:
        tail = (result.log_tail or "").strip()[-MAX_NOTE:]
        return run, reason + (f". Its output ended with:\n{tail}" if tail else "")
    return run, None


def run_reproduction(
    ctx: RunContext,
    state: PipelineState,
    call: Callable[[str], Contract | None],
    save: Callable[[], None],
) -> str:
    """The ``reproduce`` stage. ``call(note)`` runs the debugger once and returns its ``Repro``.

    Raises :class:`NeedsInfo` when no attempt (``fix.max_repro_attempts``) fails for a real
    reason, unless the run was started with ``--allow-unreproduced``. Returns a one-line summary.
    """

    wanted: FixInput = read_fix_input(ctx.run_dir)
    record = _opening_record(ctx, state, wanted)
    state.fix = record
    state.needs_info = []
    attempts = ctx.settings.fix.max_repro_attempts
    note = _first_note(record)
    questions: list[str] = []
    for number in range(1, attempts + 1):
        check_cancelled(ctx)
        record.attempts = number
        try:
            output = call(note)
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # an agent that returned nothing usable is a failed attempt
            record.notes.append(f"Attempt {number}: the debugger returned no reproduction ({exc})")
            note = f"Attempt {number} of {attempts} returned no usable reproduction: {exc}"[
                :MAX_NOTE
            ]
            continue
        repro = output if isinstance(output, Repro) else None
        if repro is None:
            note = f"Attempt {number} of {attempts} returned no reproduction."
            record.notes.append(note)
            continue
        state.repro = repro
        questions = repro.questions or questions
        red, why = _attempt(ctx, repro, number)
        if why is None and red is not None:
            return _accept(ctx, record, repro, red, number)
        record.notes.append(f"Attempt {number}: {why}")
        ctx.events.emit("fix.not_red", attempt=number, reason=(why or "")[:300])
        note = (
            f"Attempt {number} of {attempts} did not reproduce the bug: {why}\n"
            "Write a reproduction that fails because of the bug itself, run it yourself to "
            "see it fail, and return its exact command."
        )[: MAX_NOTE * 2]
        save()
    if state.repro is None:
        state.repro = Repro(summary="No reproduction was made.")
    return _not_reproduced(ctx, state, record, questions)


def _opening_record(ctx: RunContext, state: PipelineState, given: FixInput) -> FixRecord:
    trace = parse_trace(given.trace or "")
    record = FixRecord(
        allow_unreproduced=given.allow_unreproduced,
        user_repro=given.repro,
        trace=trace,
        suspects=suspect_files(trace, ctx.workspace.root),
    )
    if given.repro:  # what the person's own command does on the tree before any change
        try:
            argv = shlex.split(given.repro)
            result = _run(ctx, USER_REPRO_ID, given.repro, "user", "repro-user-before")
            if not_a_failure(argv, result) is None:
                record.user_red = _record(result, "red", given.repro, 0)
            else:
                record.notes.append(
                    f"Your command `{given.repro}` did not fail on the project as it is: "
                    f"{not_a_failure(argv, result)}."
                )
        except (ValueError, WorkspaceError) as exc:
            record.notes.append(f"Your command `{given.repro}` could not be run: {exc}")
    return record


def _first_note(record: FixRecord) -> str:
    return "\n".join(record.notes)[:MAX_NOTE]


def _accept(ctx: RunContext, record: FixRecord, repro: Repro, red: ReproRun, number: int) -> str:
    record.reproduced = True
    record.command = repro.command
    record.files = list(dict.fromkeys(repro.files))
    record.digests = {name: _digest(ctx.workspace.root, name) for name in record.files}
    record.red = red.model_copy(update={"revision": verification_revision(ctx.workspace)})
    ctx.events.emit(
        "fix.red",
        command=record.command,
        files=record.files,
        exit_code=red.exit_code,
        attempt=number,
        log_path=red.log_path,
    )
    return (
        f"Reproduced on attempt {number}: `{record.command}` fails ({red.summary}) "
        f"before any fix. {len(record.files)} reproduction file(s) pinned."
    )


def _not_reproduced(
    ctx: RunContext, state: PipelineState, record: FixRecord, questions: list[str]
) -> str:
    reasons = "; ".join(record.notes[-3:]) or "no attempt ran"
    if record.allow_unreproduced:
        ctx.events.emit("fix.unreproduced", attempts=record.attempts, allowed=True)
        return (
            f"Not reproduced after {record.attempts} attempt(s) ({reasons[:300]}). Going on "
            "without a failing test because --allow-unreproduced was given."
        )
    asked = list(dict.fromkeys([*questions, *DEFAULT_QUESTIONS]))[:6]
    state.needs_info = asked
    ctx.events.emit("fix.needs_info", attempts=record.attempts, questions=asked)
    raise NeedsInfo(
        f"The bug was not reproduced in {record.attempts} attempt(s) ({reasons[:300]}), so "
        "nothing was changed. Questions: " + " ".join(f"({i}) {q}" for i, q in enumerate(asked, 1)),
        asked,
    )


# -- verification: green, and the reproduction left alone ---------------------------------------


def fix_checks(ctx: RunContext, state: PipelineState) -> list[CheckSpec]:
    """The reproduction as required checks of the verify stage (none for an unreproduced bug)."""

    record = state.fix
    if record is None:
        return []
    checks: list[CheckSpec] = []
    if record.reproduced:
        checks.append(_spec(ctx, REPRO_ID, record.command, "plan"))
    if record.user_repro and record.user_red is not None:
        checks.append(_spec(ctx, USER_REPRO_ID, record.user_repro, "user"))
    return checks


def tampered(ctx: RunContext, state: PipelineState) -> list[str]:
    """The reproduction files that changed or went missing since they were seen failing."""

    record = state.fix
    if record is None or not record.reproduced:
        return []
    return [
        name
        for name, digest in record.digests.items()
        if _digest(ctx.workspace.root, name) != digest
    ]


def protected_paths(state: PipelineState) -> tuple[str, ...]:
    """Paths no agent may change once the bug has been seen failing."""

    record = state.fix
    return tuple(record.files) if record is not None and record.reproduced else ()


def record_green(ctx: RunContext, state: PipelineState, results: Sequence[CheckResult]) -> None:
    """Note in the record (and the event log) whether the reproduction passes now."""

    record = state.fix
    if record is None or not record.reproduced:
        return
    mine = next((r for r in results if r.id == REPRO_ID), None)
    if mine is None:
        return
    if mine.status == "passed" and not tampered(ctx, state):
        record.green = _record(mine, "green", record.command, record.attempts)
        ctx.events.emit(
            "fix.green", command=record.command, exit_code=mine.exit_code, log_path=mine.log_path
        )
    else:
        record.green = None
