"""Diff noise: how much of a change landed outside the places it was meant to be.

A change to an existing project should touch what the plan said it would touch (the work
packages' owned paths) and the tests that go with it, and little else. Lines changed anywhere
else (a reformatted neighbour, a lockfile nobody asked for, a stray script) are noise: they make
the diff harder to review and more likely to conflict. The controller measures it from Git, not
from what an agent says.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from pydantic import Field

from engineering_team.contracts import Contract, Plan
from engineering_team.modes.repo_analyzer import TEST_FILE
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.tools.scope import WriteScope

MAX_LISTED = 25


class NoisyFile(Contract):
    path: str
    lines: int


class DiffNoise(Contract):
    total_lines: int = 0
    outside_lines: int = 0
    files: int = 0
    outside: list[NoisyFile] = Field(default_factory=list)  # most lines first, capped
    outside_files: int = 0
    scope: list[str] = Field(default_factory=list)

    @property
    def ratio(self) -> float:
        return round(self.outside_lines / self.total_lines, 3) if self.total_lines else 0.0


def touched_scope(plan: Plan | None, profile: RepoProfile | None) -> list[str]:
    """The paths a change is expected to touch: the plan's owned paths and the test directories."""

    scope: list[str] = []
    if plan is not None:
        scope += [path for package in plan.work_packages for path in package.owned_paths]
    if profile is not None:
        scope += [f"/{directory}/" for directory in profile.test_dirs if directory != "."]
    return list(dict.fromkeys(scope))


def measure(changes: Iterable[tuple[str, int]], scope: Sequence[str]) -> DiffNoise:
    """Noise for ``changes`` (``(path, lines touched)``) against ``scope`` globs. A test file
    (by its name) is in scope wherever it is."""

    allowed = WriteScope(allow=tuple(scope))
    total = files = 0
    outside: list[NoisyFile] = []
    for path, lines in changes:
        total += lines
        files += 1
        if allowed.permits(path) or TEST_FILE.match(PurePosixPath(path).name):
            continue
        outside.append(NoisyFile(path=path, lines=lines))
    outside.sort(key=lambda item: (-item.lines, item.path))
    return DiffNoise(
        total_lines=total,
        outside_lines=sum(item.lines for item in outside),
        files=files,
        outside=outside[:MAX_LISTED],
        outside_files=len(outside),
        scope=list(scope),
    )
