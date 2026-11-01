"""The named conditions a recipe stage can be skipped on (``skip_if``).

Each looks at the pipeline's state and the run's settings, never at what an agent said; a skipped
stage is recorded with the condition that skipped it (``pipeline/flow.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from engineering_team.pipeline.lean import LEAN_CONDITIONS

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.settings import Settings

CONDITIONS: dict[str, Callable[[PipelineState, Settings], bool]] = {
    "no_work_packages": lambda state, _: state.plan is not None and not state.plan.work_packages,
    # The smoke profile and ``team_profile = "minimal"`` run only the essential stages.
    # maintain's security audit only changes code when asked to (``--fix``) and has findings.
    "fixes_not_requested": lambda state, _: not state.options.get("fix_findings"),
    "fixes_requested": lambda state, _: bool(state.options.get("fix_findings")),
    "no_findings": lambda state, _: not state.findings and not state.audit_findings,
    # ``review`` found no change to look at (a clean branch, nothing against the base).
    "nothing_to_review": lambda state, _: bool((state.isolation or {}).get("empty")),
    "minimal_team": lambda _, settings: (
        settings.profile == "smoke" or settings.team_profile == "minimal"
    ),
    # The lean team's: the task's size, a one-package plan, ceremony nobody asked for.
    **LEAN_CONDITIONS,
    # The ``team`` strategy's security review: only when the controller saw a security signal
    # and no security review has read the project as it is now (``pipeline/lead.py``).
    "no_security_signal": lambda state, _: (
        state.lead is None
        or not state.lead.signals
        or state.lead.security_reviewed == state.lead.handed_back
    ),
    # Acceptance tests first (``modes/acceptance.py``): the person said ``--no-acceptance``, or
    # the run is on a rung the ladder escalated to, whose workspace already holds the rung
    # before's change, so it is no longer the baseline the tests must fail on.
    "no_acceptance": lambda state, _: bool(state.options.get("no_acceptance")),
    "escalated": lambda state, _: bool(state.escalation),
}
