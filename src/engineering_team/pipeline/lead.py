"""The ``team`` strategy: one lead agent does the work and calls teammates on demand.

The lead is one long session of the generalist (the ``lead`` stage kind) with the full toolbelt
and, while the run allows delegation, the ``team`` tools. Its context stays warm for the whole
task; teammates are called only for bounded, typed jobs (``pipeline/delegation.py``), each with a
brief instead of the history. The controller keeps what it owns in every strategy: the skeleton
before the lead (profile, baseline, reproduction), the checks after it and after every delegated
job that changed files, the board, and the verdict. Unlike 0.2.0's ``hierarchical`` manager,
the lead has tools and works itself, and how much it may delegate is capped.

:func:`lead_recipe` turns any recipe with a verify stage into the lead's: the controller and
reproduce stages before verification stay, every stage that does the work (design, packages,
tests, reviews, the map) becomes the one ``lead`` stage, verification and the controller's write-up
stages stay, and a security review runs after verification only when a deterministic signal fired
(``config/lead.yaml``) and no security review read the final change.
"""

from __future__ import annotations

import functools
import hashlib
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

import yaml

from engineering_team.pipeline.recipes import Recipe, RecipeError, StageSpec
from engineering_team.team.registry import GENERALIST

if TYPE_CHECKING:
    from engineering_team.pipeline.state import PipelineState
    from engineering_team.runtime.context import RunContext

LEAD_STAGE = "lead"
SECURITY_STAGE = "security_review"
SECURITY_REVIEWER = "security_engineer"
SKELETON_BEFORE = ("controller", "reproduce")  # kept before the lead; the rest is its work
MAX_SCANNED_BYTES = 200_000  # per changed file, when looking for security signals
MAX_CRITERIA = 20


@functools.cache
def lead_config() -> dict[str, Any]:
    text = (resources.files("engineering_team") / "config" / "lead.yaml").read_text("utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


@functools.cache
def _signals() -> tuple[tuple[str, re.Pattern[str]], ...]:
    found = lead_config().get("security_signals") or {}
    return tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in found.items())


def lead_recipe(base: Recipe, lead: str = GENERALIST) -> Recipe:
    """``base`` as the lead's recipe (see the module docstring); deterministic, so a run of it
    resumes under the same digest."""

    position = next((i for i, s in enumerate(base.stages) if s.kind == "verify"), None)
    if position is None:
        raise RecipeError(
            f"Recipe '{base.name}' has no verify stage, so the team strategy cannot run it (the "
            "controller verifies what the lead hands back). Use --strategy pipeline."
        )
    produced = {"request"}

    def kept(stage: StageSpec, **update: Any) -> StageSpec:
        inputs = [name for name in stage.inputs if name in produced]
        copy = stage.model_copy(update={"inputs": inputs, **update})
        produced.update(copy.contract_outputs)
        return copy

    before = [
        kept(s, teammates=[lead]) if s.kind == "reproduce" else kept(s)
        for s in base.stages[:position]
        if s.kind in SKELETON_BEFORE
    ]
    new_project = base.mode == "new"
    lead_stage = StageSpec(
        name=LEAD_STAGE,
        kind="lead",
        teammates=[lead],
        inputs=[name for name in ("request", "repro") if name in produced],
        outputs=["file:README.md"] if new_project else [],
        verification_policy="artifacts" if new_project else "none",
        retry=1,
        description="The lead does the work and calls teammates for bounded jobs.",
        why="The team strategy: one lead session replaces the stages that do the work.",
    )
    verify = kept(base.stages[position], teammates=[lead])
    review = next((s for s in base.stages if s.kind == "review"), None)
    security = StageSpec(
        name=SECURITY_STAGE,
        kind="review",
        teammates=[SECURITY_REVIEWER],
        inputs=[name for name in (review.inputs if review else []) if name in produced],
        outputs=list(review.outputs) if review else [],
        verification_policy=review.verification_policy if review else "none",
        prompt=review.prompt if review else None,
        skip_if=["no_security_signal"],
        optional=True,
        why="A security signal fired and no security review read the final change.",
    )
    after = [kept(s) for s in base.stages[position + 1 :] if s.kind == "controller"]
    try:
        recipe = Recipe(
            name=f"team-{base.name}",
            description=f"{base.description} (lead and teammates on demand)".strip(),
            mode=base.mode,
            policies=list(base.policies),
            stages=[*before, lead_stage, verify, security, *after],
        )
    except ValueError as exc:
        raise RecipeError(f"Recipe '{base.name}' cannot be run by a lead: {exc}") from exc
    recipe.source, recipe.path = base.source, base.path
    return recipe


def security_signals(ctx: RunContext, requirements: str) -> list[str]:
    """The security signals (``config/lead.yaml``) in the request or in any file the run added or
    changed, as ``name (where)``, in a stable order."""

    texts: list[tuple[str, str]] = [("request", requirements)]
    for name, path in _changed_files(ctx):
        try:
            data = path.read_bytes()[:MAX_SCANNED_BYTES]
        except OSError:
            continue
        texts.append((name, data.decode("utf-8", errors="replace")))
    found: list[str] = []
    for signal, pattern in _signals():
        where = next((label for label, text in texts if pattern.search(text)), None)
        if where is not None:
            found.append(f"{signal} ({where})")
    return found


def _changed_files(ctx: RunContext) -> list[tuple[str, Any]]:
    from engineering_team.tools.ignore import IgnoreRules, iter_files

    workspace, baseline = ctx.workspace, ctx.baseline.files
    changed = []
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        name = workspace.relative_name(path)
        known = baseline.get(name)
        if known is None:
            changed.append((name, path))
            continue
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != known.sha256:
                changed.append((name, path))
        except OSError:
            continue
    return sorted(changed, key=lambda item: item[0])


def lead_brief(ctx: RunContext, state: PipelineState, lead: str, cap: int) -> str:
    """The lead's ``{lead}`` text: what the controller established, the team, when to call it."""

    parts = _skeleton(state)
    if cap <= 0:
        parts += ["", "You work alone in this run: no teammate can be called."]
        return "\n".join(parts).strip()
    team = [
        f"- {member.key}: {member.role_for(ctx.settings.project_name)}"
        for member in ctx.team.enabled()
        if member.key != lead and ctx.team.usable(member.key)
    ]
    parts += [
        "",
        f"Teammates you can call with the team tools (up to {cap} jobs in this run; each brief of "
        "Delegate Parallel counts; teammates cannot delegate):",
        *team,
        "",
        "When to call a teammate:",
        *(f"- {line}" for line in lead_config().get("guidance") or []),
        "",
        "The controller runs the project's checks after every job that changed files and after "
        "you hand back; you never mark anything done. A teammate's summary is its own account, "
        "not evidence.",
    ]
    return "\n".join(parts).strip()


def _skeleton(state: PipelineState) -> list[str]:
    """What the controller's own stages found before the lead started (facts, not a plan)."""

    lines: list[str] = []
    if state.baseline is not None:
        lines += ["The project's own checks before any change (the baseline):"]
        lines += [f"- {line}" for line in state.baseline.lines()]
    if state.fix is not None and state.repro is not None:
        command = state.repro.command
        lines += [
            "",
            "The reproduction the controller saw failing (it must pass when you are done; do "
            f"not edit it): {', '.join(state.repro.files) or command}"
            + (f" (run: {command})" if command else ""),
        ]
    if state.spec is not None and state.spec.criteria:
        lines += ["", "Acceptance criteria:"]
        lines += [f"- {c.id}: {c.text}" for c in state.spec.criteria[:MAX_CRITERIA]]
    return lines


def resumed_note(state: PipelineState) -> str:
    """For a lead that starts again: the jobs earlier sessions gave out, so none is redone."""

    if not state.delegations:
        return ""
    lines = ["Jobs you gave teammates in an earlier session of this run (the controller's record):"]
    for record in state.delegations:
        files = f"; changed {', '.join(record.files_changed[:8])}" if record.files_changed else ""
        lines.append(
            f"- {record.id} {record.kind} -> {record.teammate}: {record.status}{files}. "
            f"Brief: {' '.join(record.brief.split())[:200]}"
        )
    return "\n".join(lines)
