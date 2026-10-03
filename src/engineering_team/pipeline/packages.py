"""Work-package ordering, layering, and the plan checks that make parallel work safe.

Parallelism is by *ownership*: each package owns path globs, its agent may write only there,
and two packages that could run together must own disjoint paths. Shared files (manifests,
lockfiles, the README, CI configuration at the project root) belong to ``foundation`` and
``integrate`` only; no package may own them.
"""

from __future__ import annotations

from collections.abc import Sequence

from engineering_team.contracts import Plan, Spec, WorkPackage
from engineering_team.tools.scope import compile_glob

# Root-level files that several packages would otherwise fight over. Matched at the project
# root only: a ``package.json`` inside ``frontend/`` belongs to the package that owns ``frontend``.
SHARED_FILES: tuple[str, ...] = (
    "README.md",
    "README",
    "LICENSE",
    ".gitignore",
    ".env.example",
    "Makefile",
    "justfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    ".github/workflows/ci.yml",
    ".gitlab-ci.yml",
)
SHARED_DENY: tuple[str, ...] = tuple(f"/{name}" for name in SHARED_FILES)


class PlanError(ValueError):
    """The architect's plan cannot be executed; the message lists every problem found."""


def plan_problems(plan: Plan, spec: Spec | None = None, *, allow_shared: bool = False) -> list[str]:
    """Everything wrong with the plan's work packages (empty when it can be run).

    With ``allow_shared`` a package may own the project's shared root files (a change to an
    existing project has no foundation stage to own them).

    Overlapping ``owned_paths`` are *not* a problem: packages that overlap are run one after
    the other (see :func:`schedule`). With a ``spec`` every package must deliver at least one
    of its criteria.
    """

    problems: list[str] = []
    seen: set[str] = set()
    for package in plan.work_packages:
        if not package.id.strip():
            problems.append("a work package has no id")
        elif package.id in seen:
            problems.append(f"work package id {package.id!r} is used twice")
        seen.add(package.id)
    known = {criterion.id for criterion in spec.criteria} if spec is not None else None
    broken_dependencies = False
    for package in plan.work_packages:
        for dependency in package.depends_on:
            if dependency == package.id:
                problems.append(f"{package.id} depends on itself")
                broken_dependencies = True
            elif dependency not in seen:
                problems.append(f"{package.id} depends on unknown package {dependency!r}")
                broken_dependencies = True
        problems.extend(_ownership_problems(package, allow_shared))
        if spec is not None and known is not None:
            if not package.criteria_ids:
                problems.append(f"{package.id} delivers no acceptance criterion (criteria_ids)")
            problems.extend(
                f"{package.id} names unknown criterion {cid!r}"
                for cid in package.criteria_ids
                if cid not in known
            )
    if not broken_dependencies:  # a cycle is only meaningful among dependencies that exist
        try:
            order_packages(plan)
        except PlanError as exc:
            problems.append(str(exc))
    return problems


def _ownership_problems(package: WorkPackage, allow_shared: bool = False) -> list[str]:
    if not package.owned_paths:
        return [f"{package.id} owns no paths, so it could not write anything (owned_paths)"]
    problems = []
    for pattern in () if allow_shared else package.owned_paths:
        claimed = [name for name in SHARED_FILES if compile_glob(pattern).fullmatch(name)]
        if claimed:
            problems.append(
                f"{package.id} owns {pattern!r}, which covers shared file(s) "
                f"{', '.join(claimed[:3])}; shared files belong to foundation and integrate"
            )
    return problems


def order_packages(plan: Plan) -> list[WorkPackage]:
    """The packages in a runnable order: dependencies first, otherwise the plan's own order."""

    return [package for layer in layers(plan.work_packages) for package in layer]


def layers(packages: Sequence[WorkPackage]) -> list[list[WorkPackage]]:
    """Topological layers: layer *n* holds the packages whose dependencies are all in earlier
    layers, each layer in the plan's own order. Raises :class:`PlanError` on a cycle."""

    remaining = list(packages)
    done: set[str] = set()
    result: list[list[WorkPackage]] = []
    while remaining:
        ready = [p for p in remaining if all(d in done for d in p.depends_on)]
        if not ready:
            cycle = ", ".join(p.id for p in remaining)
            raise PlanError(f"the work packages {cycle} depend on each other in a cycle")
        result.append(ready)
        done.update(p.id for p in ready)
        remaining = [p for p in remaining if p not in ready]
    return result


def _prefix(pattern: str) -> list[str]:
    """The path segments a glob is certain to start with (empty: it may match anywhere)."""

    body = pattern.strip()
    anchored = body.startswith("/") or "/" in body.rstrip("/")
    if not anchored:
        return []  # no slash: gitignore matches it at any depth
    body = body.strip("/")
    cut = min((body.find(c) for c in "*?[" if c in body), default=-1)
    if cut >= 0:
        body = body[:cut].rpartition("/")[0]  # a half-written segment is not certain
    return [segment for segment in body.split("/") if segment]


def _bare_name(pattern: str) -> bool:
    """A plain name with no slash and no wildcard (``tests``, ``setup.py``)."""

    body = pattern.strip().strip("/")
    return (
        bool(body) and "/" not in pattern.strip().rstrip("/") and not any(c in body for c in "*?[")
    )


def paths_overlap(first: Sequence[str], second: Sequence[str]) -> bool:
    """Whether two sets of ownership globs might cover the same file.

    Conservative: a glob is reduced to the directory it is anchored in, and two globs overlap
    when one directory contains the other. It may call disjoint globs overlapping (the packages
    are then merely run one after the other) but never the reverse.
    """

    for a in first:
        for b in second:
            if _bare_name(a) and _bare_name(b):
                if a.strip("/") == b.strip("/"):
                    return True
                continue  # two different file or directory names anywhere cannot be one path
            left, right = _prefix(a), _prefix(b)
            shorter = min(len(left), len(right))
            if left[:shorter] == right[:shorter]:
                return True
    return False


def schedule(layer: Sequence[WorkPackage]) -> list[list[WorkPackage]]:
    """Split one layer into batches whose packages own disjoint paths (each package goes in the
    first batch it does not overlap, so plan order is kept). One batch means full parallelism."""

    batches: list[list[WorkPackage]] = []
    for package in layer:
        for batch in batches:
            if not any(paths_overlap(package.owned_paths, other.owned_paths) for other in batch):
                batch.append(package)
                break
        else:
            batches.append([package])
    return batches
