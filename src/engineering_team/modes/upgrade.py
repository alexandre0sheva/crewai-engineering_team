"""``upgrade-deps``: try upgrades in groups, undo what breaks, and bisect to find the culprit.

The planning agent proposes upgrades; the controller decides which of them stay. A group is
applied by the upgrade agent (manifests only), installed by the controller, and checked with the
project's own checks. A group that passes is committed; one that does not is undone
(``GitPort.restore``) and split in two, so one bad upgrade among many costs about ``2 log n``
attempts rather than ``n``, and each one that cannot go in is reported with what the checks said.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from engineering_team.devtools.detect import find_stacks
from engineering_team.devtools.runner import DevRunner
from engineering_team.git.port import GitError
from engineering_team.modes.maintain_contracts import Upgrade, UpgradeOutcome
from engineering_team.modes.policies import is_manifest_path
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.cancel import RunCancelled, check_cancelled
from engineering_team.runtime.context import RunContext
from engineering_team.tools.support import ToolError
from engineering_team.verification.profiles import project_roots
from engineering_team.verification.quick import quick_verify

if TYPE_CHECKING:  # the pipeline imports this module
    from engineering_team.pipeline.state import PipelineState

T = TypeVar("T")
MAX_REASON = 400
MAX_LABELS = 5


@dataclass(frozen=True)
class Attempt:
    ok: bool
    reason: str = ""


@dataclass
class BisectOutcome(Generic[T]):
    accepted: list[T] = field(default_factory=list)
    rejected: list[tuple[T, str]] = field(default_factory=list)


def bisect_upgrades(
    items: Sequence[T],
    attempt: Callable[[list[T]], Attempt],
    *,
    group_size: int,
    on_accepted: Callable[[list[T]], None] | None = None,
    on_rejected: Callable[[T, str], None] | None = None,
) -> BisectOutcome[T]:
    """Take ``items`` in groups of ``group_size``. ``attempt(group)`` applies the group on top of
    what was accepted so far, checks it, and undoes it when it fails. A failing group of one is
    rejected with the attempt's reason; a larger one is split in half and each half is tried on
    its own, the first half's result standing before the second is tried."""

    outcome: BisectOutcome[T] = BisectOutcome()

    def process(group: list[T]) -> None:
        result = attempt(group)
        if result.ok:
            outcome.accepted.extend(group)
            if on_accepted is not None:
                on_accepted(group)
        elif len(group) == 1:
            outcome.rejected.append((group[0], result.reason))
            if on_rejected is not None:
                on_rejected(group[0], result.reason)
        else:
            middle = len(group) // 2
            process(group[:middle])
            process(group[middle:])

    step = max(1, group_size)
    for start in range(0, len(items), step):
        process(list(items[start : start + step]))
    return outcome


# -- the controller's side of one attempt --------------------------------------------------------


def _outside_manifests(ctx: RunContext) -> list[str]:
    """Files changed since the last commit that are not manifests or lockfiles."""

    return [c.path for c in ctx.git.changes("HEAD") if not is_manifest_path(c.path)]


def _install(ctx: RunContext) -> str:
    """Install the declared dependencies of each project (the upgraded ones); the reason it
    failed, or an empty string."""

    runner = DevRunner(ctx)
    for stack in project_roots(find_stacks(ctx.workspace.root)):
        try:
            report = runner.install(stack.directory)
        except ToolError as exc:
            return str(exc)[:MAX_REASON]
        if report.status == "passed":
            continue
        if report.status == "unavailable":
            return f"{stack.manager} is not available: {report.hint or ''}".strip()[:MAX_REASON]
        why = report.hint or " ".join(report.raw_tail.split())[-250:]
        return f"installing in {stack.directory} failed ({stack.manager}): {why}"[:MAX_REASON]
    return ""


def run_upgrades(
    ctx: RunContext,
    state: PipelineState,
    apply: Callable[[list[Upgrade], int], None],
    save: Callable[[], None],
) -> str:
    """Try every planned upgrade that has no outcome yet; returns a one-line summary.

    ``apply(group, number)`` has the upgrade agent edit the manifests for ``group``. Outcomes are
    recorded as they are decided (and the state saved), so a resumed run does not try again what
    was already decided.
    """

    plan = state.upgrades
    if plan is None or not plan.upgrades:
        return "Nothing to upgrade: the plan lists no upgrade."
    if not ctx.git.is_repo():
        raise GitError("upgrade-deps needs the project to be a Git repository to undo a failure.")
    decided = {o.upgrade.label for o in state.upgrade_outcomes}
    pending = [u for u in plan.upgrades if u.label not in decided]
    counter = {"n": len(state.upgrade_outcomes)}

    def attempt(group: list[Upgrade]) -> Attempt:
        check_cancelled(ctx)
        counter["n"] += 1
        number = counter["n"]
        ctx.events.emit("upgrade.attempt", number=number, upgrades=[u.label for u in group])
        reason = ""
        try:
            apply(group, number)
            stray = _outside_manifests(ctx)
            if stray:
                reason = "it changed files that are not manifests or lockfiles: " + ", ".join(
                    stray[:6]
                )
            if not reason:
                reason = _install(ctx)
            if not reason:
                ok, reason = quick_verify(ctx, state, f"upgrade-{number}")
                if ok:
                    ctx.git.checkpoint(_subject(group))
                    return Attempt(True)
        except (RunCancelled, BudgetExceeded):
            ctx.git.restore("HEAD")
            raise
        except Exception as exc:  # an agent that crashed is an attempt that failed
            reason = f"the upgrade agent failed: {type(exc).__name__}: {exc}"[:MAX_REASON]
        ctx.git.restore("HEAD")
        ctx.events.emit("upgrade.undone", number=number, reason=reason[:300])
        return Attempt(False, reason)

    def accepted(group: list[Upgrade]) -> None:
        number = counter["n"]
        state.upgrade_outcomes += [
            UpgradeOutcome(upgrade=u, status="upgraded", group=number) for u in group
        ]
        save()

    def rejected(item: Upgrade, reason: str) -> None:
        state.upgrade_outcomes.append(
            UpgradeOutcome(upgrade=item, status="failed", reason=reason, group=counter["n"])
        )
        save()

    outcome = bisect_upgrades(
        pending,
        attempt,
        group_size=ctx.settings.maintain.upgrade_group_size,
        on_accepted=accepted,
        on_rejected=rejected,
    )
    total = len(state.upgrade_outcomes)
    done = sum(1 for o in state.upgrade_outcomes if o.status == "upgraded")
    return (
        f"{done} of {total} upgrade(s) went in; {total - done} could not "
        f"({len(outcome.rejected)} rejected this time), in {counter['n']} attempt(s)."
    )


def _subject(group: Sequence[Upgrade]) -> str:
    labels = [u.label for u in group[:MAX_LABELS]]
    more = f" and {len(group) - MAX_LABELS} more" if len(group) > MAX_LABELS else ""
    return f"upgrade: {', '.join(labels)}{more}"[:72]
