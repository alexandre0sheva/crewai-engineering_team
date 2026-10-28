"""The lean team: how big a task is, and what that lets the controller leave out.

The size decides the structure of a run before any model is called, from deterministic signals
only (the request's length, how much code the repository already has, the plan's packages once
it exists; ``lean.size`` overrides them):

* ``small``: one design session writes the spec and the plan, the plan collapses into one work
  package (so one implementer does foundation, packages and integration), a fix skips triage and
  the codebase map, one reviewer reads the change on the worker tier;
* ``medium``: the design is still one session; packages, foundation and integration run as
  planned, every reviewer reads the change;
* ``large``: spec and plan are separate sessions, the plan on the architect's own tier.

Ceremony (``devops``, ``docs``) runs only when the request asks for it or ``lean.ceremony`` says
``always``. A skipped stage is recorded with the condition that skipped it. The thresholds are
provisional until the live evaluation (T33) measures them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from engineering_team.contracts import Contract, Plan, WorkPackage

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.settings import Settings
    from engineering_team.tools.workspace import ProjectWorkspace

TaskSize = Literal["small", "medium", "large"]
SIZES: tuple[TaskSize, ...] = ("small", "medium", "large")

# Provisional thresholds (T33 measures them).
LARGE_REQUEST_WORDS = 600
MEDIUM_REPO_FILES = 40  # an existing project with more files than this is at least medium
LARGE_REPO_FILES = 400
MEDIUM_PACKAGES = 3  # a plan with this many packages is at least medium
LARGE_PACKAGES = 6
REPO_SCAN_LIMIT = LARGE_REPO_FILES + 1

CEREMONY: dict[str, re.Pattern[str]] = {
    "devops": re.compile(
        r"\b(ci|ci/cd|continuous (integration|delivery|deployment)|github actions?|gitlab[- ]ci|"
        r"docker(file)?|containers?|container image|deploy(ment|ing|s)?|kubernetes|k8s|helm|"
        r"devops)\b",
        re.IGNORECASE,
    ),
    "docs": re.compile(
        r"\b(documentation|docs|user guide|usage guide|tutorial|manual|api reference|"
        r"technical writer)\b",
        re.IGNORECASE,
    ),
}
# A request touching any of these is high-risk: the security reviewer and the architect's own
# tier stay, whatever the size.
RISK = re.compile(
    r"\b(auth\w*|login|log in|passwords?|secrets?|tokens?|crypt\w*|encrypt\w*|payments?|billing|"
    r"uploads?|sql|permissions?|security|sessions?|cookies?|oauth|jwt|pii|personal data)\b",
    re.IGNORECASE,
)
SECURITY_REVIEWER = "security_engineer"
# The modes whose recipes are sized; a ``review`` or ``maintain`` run keeps every reviewer it asks
# for and every teammate's own tier.
LEAN_MODES = frozenset({"new", "feature", "fix"})
LEAN_TIER = "worker"
COLLAPSED_ID = "WP-1"


class LeanProfile(Contract):
    """What the controller decided about a run's structure, and why (kept in the state)."""

    size: TaskSize = "medium"
    high_risk: bool = False
    ceremony: list[str] = []  # the ceremony stages the request (or ``lean.ceremony``) asks for
    reasons: list[str] = []


def _larger(a: TaskSize, b: TaskSize) -> TaskSize:
    return a if SIZES.index(a) >= SIZES.index(b) else b


def count_files(workspace: ProjectWorkspace, limit: int = REPO_SCAN_LIMIT) -> int:
    """Files in the project (ignored paths left out), counted up to ``limit``."""

    from engineering_team.tools.ignore import iter_files

    count = 0
    for _ in iter_files(workspace):
        count += 1
        if count >= limit:
            break
    return count


def assess(requirements: str, settings: Settings, existing_files: int) -> LeanProfile:
    """The profile before any model is called: request, repository, settings."""

    lean = settings.lean
    words = len(requirements.split())
    size: TaskSize = "small"
    reasons = [f"request: {words} words"]
    if words > LARGE_REQUEST_WORDS:
        size = "large"
    if existing_files:
        reasons.append(
            f"repository: {existing_files}{'+' if existing_files >= REPO_SCAN_LIMIT else ''} files"
        )
        if existing_files > LARGE_REPO_FILES:
            size = _larger(size, "large")
        elif existing_files > MEDIUM_REPO_FILES:
            size = _larger(size, "medium")
    if lean.size != "auto":
        size = lean.size
        reasons.append(f"lean.size = {lean.size}")
    if lean.ceremony == "always":
        ceremony = sorted(CEREMONY)
    elif lean.ceremony == "never":
        ceremony = []
    else:
        ceremony = [
            name for name, pattern in sorted(CEREMONY.items()) if pattern.search(requirements)
        ]
    risky = bool(RISK.search(requirements))
    if risky:
        reasons.append("high risk: the request touches security-sensitive behaviour")
    return LeanProfile(size=size, high_risk=risky, ceremony=ceremony, reasons=reasons)


def refine(profile: LeanProfile, plan: Plan, settings: Settings) -> LeanProfile:
    """The profile once the plan exists: more packages make a task larger, never smaller."""

    if settings.lean.size != "auto":
        return profile
    count = len(plan.work_packages)
    planned: TaskSize = (
        "large" if count >= LARGE_PACKAGES else "medium" if count >= MEDIUM_PACKAGES else "small"
    )
    size = _larger(profile.size, planned)
    if size == profile.size:
        return profile
    return profile.model_copy(
        update={"size": size, "reasons": [*profile.reasons, f"plan: {count} work packages"]}
    )


def collapse_plan(plan: Plan) -> Plan:
    """A small task's plan as one work package with every package's paths and criteria.

    One implementer then does what foundation, the packages and integration would have done in
    several sessions; a sole package works unscoped (``package_exec``), so its paths only say
    where the change is meant to be (the diff-noise measure of a change reads them). The
    packages' descriptions stay in the one package's description, so the architect's split still
    guides the work."""

    packages = plan.work_packages
    if len(packages) <= 1:
        return plan
    criteria = list(dict.fromkeys(c for p in packages for c in p.criteria_ids))
    parts = "\n".join(
        f"- {p.id} {p.title} ({', '.join(p.owned_paths) or 'no paths'}): {p.description}".rstrip(
            ": "
        )
        for p in packages
    )
    merged = WorkPackage(
        id=COLLAPSED_ID,
        title="Build the whole plan",
        role=packages[0].role,
        description=f"Every part of the plan, in one session:\n{parts}",
        owned_paths=list(dict.fromkeys(path for p in packages for path in p.owned_paths)) or ["**"],
        criteria_ids=criteria,
        required=any(p.required for p in packages),
    )
    return plan.model_copy(update={"work_packages": [merged]})


def reviewers(profile: LeanProfile | None, teammates: list[str]) -> list[str]:
    """Who reviews: a small, low-risk change has one reviewer (the first one listed)."""

    if profile is None or profile.size != "small" or profile.high_risk or len(teammates) <= 1:
        return teammates
    kept = [key for key in teammates if key != SECURITY_REVIEWER]
    return kept[:1] or teammates[:1]


def tier_for(profile: LeanProfile | None, stage_kind: str, outputs: list[str]) -> str | None:
    """The model tier a stage's agent runs on instead of its own, or ``None`` to keep it.

    The merged design (spec and plan in one session) runs on the worker tier unless the task is
    large or high-risk; a small, low-risk review does too."""

    if profile is None or profile.high_risk or profile.size == "large":
        return None
    if stage_kind == "agent" and {"spec", "plan"} <= set(outputs):
        return LEAN_TIER
    if stage_kind == "review" and profile.size == "small":
        return LEAN_TIER
    return None


def _size(state: PipelineState) -> TaskSize | None:
    return state.lean.size if state.lean is not None else None


def _ceremony(name: str) -> Callable[[PipelineState, Settings], bool]:
    return lambda state, _: state.lean is not None and name not in state.lean.ceremony


# Named skip conditions (``recipes.CONDITIONS`` includes them).
LEAN_CONDITIONS: dict[str, Callable[[PipelineState, Settings], bool]] = {
    "large_task": lambda state, _: _size(state) == "large",
    "not_large_task": lambda state, _: _size(state) in ("small", "medium"),
    "small_task": lambda state, _: _size(state) == "small",
    "single_package": lambda state, _: (
        state.plan is not None and len(state.plan.work_packages) == 1
    ),
    "devops_not_requested": _ceremony("devops"),
    "docs_not_requested": _ceremony("docs"),
}
