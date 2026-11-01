"""Acceptance tests first: written apart from the implementers, proven red on the baseline.

The ``acceptance`` stage (before ``implement``) has the quality engineer write one or more tests
per criterion of the spec, in new test files only (its write scope is the ``tests`` lane). The
controller runs each test itself on the project as it still is, the baseline, and keeps it only
if it fails for the right reason (``acceptance_red.py``). A kept test is pinned by digest: no later
agent may write it (the write scope denies it) and an edit that gets around the scope makes the
verification fail. A rejected test goes back to the quality engineer with the reason, within
``acceptance.max_attempts``; files of a test still rejected after that are removed, and the run
goes on without it (its criteria are then proven, or not, as before).

In verification each kept test is a required check mapped to its criteria, so a criterion becomes
``verified`` when a test that was red on the baseline passes; the implementers' own tests (the
project's suite) only ever make a criterion ``referenced``.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from engineering_team.contracts import CheckResult, CheckSpec, Contract, CriterionCoverage
from engineering_team.modes.acceptance_contracts import (
    CHECK_PREFIX,
    AcceptanceRecord,
    AcceptanceSuite,
    AcceptanceTest,
    AcceptedTest,
)
from engineering_team.modes.acceptance_red import (
    command_problem,
    file_problem,
    project_module,
    red_problem,
)
from engineering_team.modes.fix_contracts import ReproRun
from engineering_team.modes.fix_repro import protected_paths
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.tools.ignore import iter_files
from engineering_team.tools.workspace import WorkspaceError
from engineering_team.verification.verifier import Verifier

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

MAX_NOTE = 2400
MAX_TAIL = 600


def run_acceptance(
    ctx: RunContext,
    state: PipelineState,
    call: Callable[[str], Contract | None],
    save: Callable[[], None],
) -> str:
    """The ``acceptance`` stage. ``call(note)`` runs the quality engineer once and returns its
    ``AcceptanceSuite``. Returns a one-line summary; never fails the run for a rejected test."""

    spec = state.spec
    # The stage's contract is always set when it ends, so a resume treats it as finished.
    state.acceptance = AcceptanceSuite()
    if spec is None or not spec.criteria:
        state.acceptance_record = AcceptanceRecord()
        return "No acceptance criteria to write tests for."
    if state.acceptance_record is not None:  # a stage that runs again starts from the baseline
        _remove(ctx, state.acceptance_record.created)
    record = AcceptanceRecord()
    state.acceptance_record = record
    known = {c.id for c in spec.criteria}
    existing = _files(ctx)
    module = project_module(ctx.workspace.root, _planned(state))
    attempts = ctx.settings.acceptance.max_attempts
    note = ""
    rejected: set[str] = set()  # files of refused tests: the next attempt may rewrite them
    for number in range(1, attempts + 1):
        check_cancelled(ctx)
        record.attempts = number
        try:
            suite = call(note)
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as exc:  # an agent that returned nothing usable is a used attempt
            suite, why = None, f"the quality engineer returned no tests ({exc})"[:MAX_TAIL]
        else:
            why = "the quality engineer returned no tests"
        record.created = sorted({*record.created, *(_files(ctx) - existing)})
        if not isinstance(suite, AcceptanceSuite) or not suite.tests:
            record.rejected.append(f"Attempt {number}: {why}")
            note = f"Attempt {number} of {attempts}: {why}. Write the tests and list them."
            continue
        state.acceptance = suite
        record.untestable = list(suite.untestable)
        refused: list[str] = []
        for index, test in enumerate(suite.tests, 1):
            if why := _try(ctx, record, test, (known, existing), module, f"{number}-{index}"):
                refused.append(
                    f"`{test.command or '(no command)'}` "
                    f"({', '.join(test.criteria) or 'no criterion'}): {why}"
                )
                rejected |= set(test.files)
        record.rejected += [f"Attempt {number}: {line}" for line in refused]
        _pin(ctx, record, rejected - set(record.files))
        save()
        missing = [c for c in sorted(known) if c not in record.covered() and not _said(suite, c)]
        if not refused and not missing:
            break
        note = _rewrite_note(record, refused, missing, number, attempts)
    _remove(ctx, sorted((rejected - set(record.files)) & set(record.created)))
    record.created = [name for name in record.created if _exists(ctx, name)]
    _pin(ctx, record, set())
    save()
    return _summary(ctx, record, known)


def _try(
    ctx: RunContext,
    record: AcceptanceRecord,
    test: AcceptanceTest,
    names: tuple[set[str], set[str]],
    module: Callable[[str], bool],
    label: str,
) -> str:
    """Run one test on the baseline; record it when red for the right reason, else say why not.
    ``names`` are the spec's criterion ids and the files the project had before the stage."""

    known, existing = names
    if test.files and set(test.files) <= set(record.files):
        return ""  # an accepted test listed again: it is pinned as it was
    unknown = [c for c in test.criteria if c not in known]
    if not test.criteria or unknown:
        return f"it names no criterion of the spec ({', '.join(unknown) or 'none'})"
    problem = file_problem(test.files, existing, lambda name: _exists(ctx, name))
    if problem:
        return problem
    try:
        argv = shlex.split(test.command)
    except ValueError as exc:
        return f"its command is not a valid command line ({exc})"
    if wrong := command_problem(argv, test.files):
        return wrong
    check_id = f"{CHECK_PREFIX}{len(record.accepted) + 1}"
    spec = _check(ctx, check_id, argv, test.criteria)
    try:
        result = Verifier(ctx).run([spec], label=f"acceptance-red-{label}")[0]
    except WorkspaceError as exc:
        return f"the controller may not run this command: {exc}"
    if why := red_problem(argv, result, known_module=module):
        tail = (result.log_tail or "").strip()[-MAX_TAIL:]
        ctx.events.emit("acceptance.rejected", command=test.command, reason=why[:300])
        return why + (f". Its output ended with:\n{tail}" if tail else "")
    red = _run(result, "red", test.command, record.attempts)
    record.accepted.append(
        AcceptedTest(
            id=check_id,
            criteria=list(dict.fromkeys(test.criteria)),
            files=list(dict.fromkeys(test.files)),
            command=test.command,
            expected_failure=test.expected_failure,
            red=red,
        )
    )
    record.revision = red.revision
    ctx.events.emit(
        "acceptance.red",
        check=check_id,
        criteria=test.criteria,
        command=test.command,
        exit_code=result.exit_code,
        log_path=result.log_path,
    )
    return ""


def _said(suite: AcceptanceSuite, criterion: str) -> bool:
    """The agent says no automated test can show ``criterion`` (its own account, for a person)."""

    return any(line.split(":", 1)[0].strip() == criterion for line in suite.untestable)


def _rewrite_note(
    record: AcceptanceRecord, refused: list[str], missing: list[str], number: int, attempts: int
) -> str:
    lines = [f"Attempt {number} of {attempts} is not done."]
    if record.accepted:
        lines.append("Accepted and read-only now (do not list or touch them again):")
        lines += [f"- {t.id} ({', '.join(t.criteria)}): `{t.command}`" for t in record.accepted]
    if refused:
        lines.append("Rejected (rewrite these; the controller ran each on the project as it is):")
        lines += [f"- {line}" for line in refused]
    if missing:
        lines.append(f"Criteria with no accepted test yet: {', '.join(missing)}.")
    lines.append("Return only the new or rewritten tests.")
    return "\n".join(lines)[:MAX_NOTE]


def _summary(ctx: RunContext, record: AcceptanceRecord, known: set[str]) -> str:
    covered = record.covered()
    if not record.accepted:
        ctx.events.emit("acceptance.none", attempts=record.attempts)
        return (
            f"No acceptance test failed on the baseline for the right reason in "
            f"{record.attempts} attempt(s); the run goes on without one."
        )
    left = sorted(known - set(covered))
    return (
        f"{len(record.accepted)} acceptance test(s) red on the baseline for "
        f"{', '.join(covered)}; pinned for the rest of the run"
        + (f"; no test for {', '.join(left)}." if left else ".")
    )


# -- files: what the stage wrote, what is pinned ------------------------------------------------


def _files(ctx: RunContext) -> set[str]:
    return {ctx.workspace.relative_name(path) for path in iter_files(ctx.workspace)}


def _exists(ctx: RunContext, name: str) -> bool:
    try:
        return ctx.workspace.resolve(name, must_exist=True).is_file()
    except WorkspaceError:
        return False


def _planned(state: PipelineState) -> list[str]:
    plan = state.plan
    return [p for package in plan.work_packages for p in package.owned_paths] if plan else []


def _digest(ctx: RunContext, name: str) -> str:
    try:
        return hashlib.sha256((ctx.workspace.root / name).read_bytes()).hexdigest()
    except OSError:
        return ""


def _pin(ctx: RunContext, record: AcceptanceRecord, rewritable: set[str]) -> None:
    """Pin what the stage wrote, except the files of refused tests (the next attempt may rewrite
    them): the accepted tests and any support file they may lean on."""

    record.digests = {
        name: _digest(ctx, name)
        for name in record.created
        if name not in rewritable and _exists(ctx, name)
    }


def _remove(ctx: RunContext, names: Sequence[str]) -> None:
    """Delete files the stage itself created (never a file the project had)."""

    for name in names:
        try:
            ctx.workspace.delete_path(name)
        except (WorkspaceError, OSError):
            continue


# -- verification: green, and the tests left alone ----------------------------------------------


def _check(ctx: RunContext, check_id: str, argv: list[str], criteria: list[str]) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        name=f"Acceptance test for {', '.join(criteria)}",
        argv=argv,
        required=True,
        timeout=float(ctx.settings.acceptance.timeout),
        criteria_ids=list(criteria),
        kind="custom",
        source="plan",  # allowlisted commands, like the plan's own
    )


def _run(result: CheckResult, phase: str, command: str, attempt: int) -> ReproRun:
    status = result.status if result.status in ("failed", "passed") else "unavailable"
    return ReproRun.model_validate(
        {
            "phase": phase,
            "command": result.command or command,
            "status": status,
            "exit_code": result.exit_code,
            "summary": result.summary,
            "log_path": result.log_path,
            "revision": result.revision,
            "attempt": attempt,
        }
    )


def acceptance_checks(ctx: RunContext, state: PipelineState) -> list[CheckSpec]:
    """The accepted tests as required checks of the verify stage, mapped to their criteria."""

    record = state.acceptance_record
    if record is None:
        return []
    return [_check(ctx, t.id, shlex.split(t.command), t.criteria) for t in record.accepted]


def acceptance_tampered(ctx: RunContext, state: PipelineState) -> list[str]:
    """The pinned files that changed or went missing since the tests were seen failing."""

    record = state.acceptance_record
    if record is None:
        return []
    return [name for name, digest in record.digests.items() if _digest(ctx, name) != digest]


def pinned_paths(state: PipelineState) -> tuple[str, ...]:
    """Paths no agent may change: a reproduction seen failing and the pinned acceptance tests."""

    record = state.acceptance_record
    return (*protected_paths(state), *(record.digests if record is not None else ()))


def record_acceptance_green(
    ctx: RunContext, state: PipelineState, results: Sequence[CheckResult]
) -> None:
    """Note per accepted test whether it passes now (its files unchanged), and say so once."""

    record = state.acceptance_record
    if record is None or not record.accepted:
        return
    by_id = {r.id: r for r in results}
    intact = not acceptance_tampered(ctx, state)
    for test in record.accepted:
        result = by_id.get(test.id)
        was_green = test.green is not None
        if result is not None and result.status == "passed" and intact:
            test.green = _run(result, "green", test.command, record.attempts)
            if not was_green:
                ctx.events.emit("acceptance.green", check=test.id, criteria=test.criteria)
        else:
            test.green = None


def annotate(coverage: list[CriterionCoverage], state: PipelineState) -> list[CriterionCoverage]:
    """Say on each criterion an acceptance test proves that the test was red on the baseline."""

    record = state.acceptance_record
    ids = {t.id for t in record.accepted} if record is not None else set()
    return [
        item.model_copy(
            update={"note": "proven by an acceptance test written apart, red on the baseline"}
        )
        if item.status == "verified" and ids & set(item.checks)
        else item
        for item in coverage
    ]


def render_acceptance_section(state: PipelineState) -> list[str]:
    """Lines (a heading first) for ``CHANGE_SUMMARY.md``; empty when the stage did not run."""

    record = state.acceptance_record
    if record is None or not record.attempts:
        return []
    lines = ["## Acceptance tests", ""]
    if not record.accepted:
        return [*lines, "- None failed on the baseline for the right reason, so none was kept."]
    for test in record.accepted:
        now = "green now" if test.green is not None else "**not green**"
        lines.append(
            f"- {test.id} ({', '.join(test.criteria)}): `{test.command}` red on the baseline "
            f"({test.red.summary or 'failed'}), {now}; files {', '.join(test.files)}"
        )
    return lines
