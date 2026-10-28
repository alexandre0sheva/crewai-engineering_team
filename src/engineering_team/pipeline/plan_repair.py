"""Fixing the architect's plan when its mistake is mechanical.

A work package may not own the project's shared root files (``packages.SHARED_FILES``: they belong
to ``foundation`` and ``integrate``). Models keep handing ``README.md`` to a package anyway, and
rejecting the whole plan for it failed runs over a detail the controller can settle by itself:

* a package that owns a shared file by name loses it (``foundation`` and ``integrate`` write it);
* a broad glob that happens to cover shared files (``**``, ``*.md``) is narrowed to the
  directories below the root;
* a package left with nothing to own is dropped, and the packages that waited for it wait for
  what it waited for.

Every change is returned as plain data for a ``plan.repaired`` event (before and after). A plan
with a mistake that cannot be fixed this way (a glob that still covers a shared file when
narrowed, or every package dropped) is returned unchanged, so ``plan_problems`` rejects it with
the original reason. Overlapping globs need no repair: packages
that might touch the same files already run one after the other (``packages.schedule``).
"""

from __future__ import annotations

from typing import Any

from engineering_team.contracts import Plan, WorkPackage
from engineering_team.pipeline.packages import SHARED_FILES
from engineering_team.tools.scope import compile_glob

Change = dict[str, Any]
WHY_SHARED = "{names} belongs to foundation and integrate"


def repair_plan(plan: Plan, *, allow_shared: bool = False) -> tuple[Plan, list[Change]]:
    """``plan`` with its ownership mistakes fixed, and what was changed (empty when nothing was,
    in which case the very same plan comes back). With ``allow_shared`` nothing is a mistake."""

    if allow_shared:
        return plan, []
    changes: list[Change] = []
    fixed: dict[str, list[str]] = {}
    for package in plan.work_packages:
        if not package.owned_paths:
            continue
        found = _fix_paths(package.owned_paths)
        if found is None:
            return plan, []
        paths, fixes = found
        if not fixes:
            continue
        fixed[package.id] = paths
        kinds = {kind for kind, _ in fixes}
        changes.append(
            {
                "package": package.id,
                "action": "dropped" if not paths else kinds.pop() if len(kinds) == 1 else "moved",
                "before": list(package.owned_paths),
                "after": list(paths),
                "why": "; ".join(dict.fromkeys(why for _, why in fixes)),
            }
        )
    dropped = {pid for pid, paths in fixed.items() if not paths}
    if not changes or len(dropped) == len(plan.work_packages):
        return plan, []
    inherited = {p.id: p.depends_on for p in plan.work_packages if p.id in dropped}
    packages = [
        _repaired(package, fixed.get(package.id, package.owned_paths), dropped, inherited)
        for package in plan.work_packages
        if package.id not in dropped
    ]
    return plan.model_copy(update={"work_packages": packages}), changes


def _repaired(
    package: WorkPackage,
    paths: list[str],
    dropped: set[str],
    inherited: dict[str, list[str]],
) -> WorkPackage:
    depends: list[str] = []
    pending = list(package.depends_on)
    while pending:
        dependency = pending.pop(0)
        if dependency in dropped:
            pending = [*inherited[dependency], *pending]  # wait for what the dropped one awaited
        elif dependency != package.id and dependency not in depends:
            depends.append(dependency)
    return package.model_copy(update={"owned_paths": paths, "depends_on": depends})


def _claimed(pattern: str) -> list[str]:
    compiled = compile_glob(pattern)
    return [name for name in SHARED_FILES if compiled.fullmatch(name)]


def _fix_paths(patterns: list[str]) -> tuple[list[str], list[tuple[str, str]]] | None:
    """The patterns without the shared root files they claim, and a ``(kind, why)`` per fix
    (``narrowed`` or ``moved``); ``None`` when a broad glob cannot be narrowed clear of them."""

    kept: list[str] = []
    fixes: list[tuple[str, str]] = []
    for pattern in patterns:
        claimed = _claimed(pattern)
        if not claimed:
            kept.append(pattern)
            continue
        if _names_one_file(pattern):
            fixes.append(("moved", WHY_SHARED.format(names=", ".join(claimed[:3]))))
            continue
        narrowed = _narrow(pattern)
        if narrowed is None:
            fixes.append(("moved", WHY_SHARED.format(names=", ".join(claimed[:3]))))
        elif _claimed(narrowed):
            return None
        else:
            kept.append(narrowed)
            why = f"{pattern} narrowed to {narrowed} (it covered {', '.join(claimed[:3])})"
            fixes.append(("narrowed", why))
    return list(dict.fromkeys(kept)), fixes


def _names_one_file(pattern: str) -> bool:
    """A shared file named outright (``README.md``, ``/pyproject.toml``): the architect meant the
    root file, so it is simply taken away rather than widened to every directory."""

    return pattern.strip().lstrip("/") in SHARED_FILES


def _narrow(pattern: str) -> str | None:
    """The pattern restricted to files below the root, or ``None`` when it only meant root files."""

    body = pattern.strip()
    if body.startswith("/"):
        rest = body[1:]
        return f"/*/{rest}" if rest.startswith("**") else None  # ``/*.toml`` is root files only
    if body.startswith("**"):
        return f"*/{body}"
    if "/" in body.rstrip("/"):
        return None  # anchored at a directory already
    return f"*/**/{body}"  # no slash: matched at any depth, root included
