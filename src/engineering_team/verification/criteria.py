"""Which acceptance criteria the controller-run checks actually prove.

A criterion is ``verified`` only when a *passing* check that is mapped to it (``criteria`` in the
user's checks file) ran. A passing test suite whose test files merely mention the criterion's id
is ``referenced``: a hint for a human, because an agent wrote both the test and the mention.
Everything else is ``unverified`` and is listed for a person in the report.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from engineering_team.contracts import CheckResult, CheckSpec, CriterionCoverage, Spec
from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.workspace import ProjectWorkspace

MAX_TEST_FILES = 400
MAX_TEST_FILE_BYTES = 200_000
TEST_DIRECTORIES = frozenset({"test", "tests", "spec", "specs", "__tests__", "e2e"})
TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.(py|go|exs?)$|\.(test|spec)\.[a-z]+$|_spec\.rb$|"
    r"(Test|Tests|Spec)\.(java|kt|php|cs)$|^test.*\.(sh|js|ts)$)"
)


def is_test_file(relative: str) -> bool:
    path = PurePosixPath(relative)
    return bool(TEST_NAME.search(path.name)) or any(
        part in TEST_DIRECTORIES for part in path.parts[:-1]
    )


def _mentions(workspace: ProjectWorkspace, ids: Sequence[str]) -> dict[str, list[str]]:
    """Criterion id -> test files that mention it as a whole token (``AC-1`` is not ``AC-10``)."""

    patterns = {i: re.compile(rf"(?<![\w-]){re.escape(i)}(?![\w-])") for i in ids}
    found: dict[str, list[str]] = {i: [] for i in ids}
    scanned = 0
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        name = workspace.relative_name(path)
        if not is_test_file(name):
            continue
        scanned += 1
        if scanned > MAX_TEST_FILES:
            break
        text = read_text_or_none(path, limit=MAX_TEST_FILE_BYTES)
        if text is None:
            continue
        for criterion, pattern in patterns.items():
            if pattern.search(text):
                found[criterion].append(name)
    return found


def map_criteria(
    workspace: ProjectWorkspace,
    spec: Spec | None,
    checks: Sequence[CheckSpec],
    results: Sequence[CheckResult],
) -> list[CriterionCoverage]:
    """The standing of every criterion of ``spec`` (in spec order)."""

    if spec is None or not spec.criteria:
        return []
    by_id = {r.id: r for r in results}
    suite_passed = any(r.kind == "test" and r.status == "passed" for r in results)
    mentioned = _mentions(workspace, [c.id for c in spec.criteria]) if suite_passed else {}
    coverage: list[CriterionCoverage] = []
    for criterion in spec.criteria:
        mapped = [c.id for c in checks if criterion.id in c.criteria_ids and c.id in by_id]
        passing = [i for i in mapped if by_id[i].status == "passed"]
        item = CriterionCoverage(id=criterion.id, text=criterion.text, checks=mapped)
        if passing:
            item = item.model_copy(update={"status": "verified", "checks": passing})
        elif mapped:
            item = item.model_copy(
                update={"note": f"mapped check(s) did not pass: {', '.join(mapped)}"}
            )
        elif files := mentioned.get(criterion.id):
            item = item.model_copy(
                update={
                    "status": "referenced",
                    "note": f"mentioned in {', '.join(files[:3])}; a passing suite is not proof",
                }
            )
        coverage.append(item)
    return coverage
