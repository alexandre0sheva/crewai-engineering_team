"""Plan auto-repair: a mechanically fixable ownership mistake is fixed and recorded, the rest
is still rejected with its reason (``pipeline/plan_repair.py``)."""

from __future__ import annotations

import pytest
from pipeline_fakes import SPEC

from engineering_team.contracts import Plan, Spec, WorkPackage
from engineering_team.pipeline.packages import plan_problems
from engineering_team.pipeline.plan_repair import repair_plan


def wp(
    id_: str, paths: list[str], *deps: str, criteria: tuple[str, ...] = ("AC-1",)
) -> WorkPackage:
    return WorkPackage(
        id=id_,
        title=id_,
        role="backend",
        owned_paths=paths,
        depends_on=list(deps),
        criteria_ids=list(criteria),
    )


def plan_of(*packages: WorkPackage) -> Plan:
    return Plan(work_packages=list(packages))


# The three plans the architect wrote in the 0.2.0 benchmark that were rejected for owning
# README.md (docs/BENCHMARKS.md, "Every failure and its cause"; the paths are the recorded ones).
BENCHMARK_PLANS: dict[str, Plan] = {
    "notes-cli, pilot": plan_of(
        wp("WP-1", ["README.md"]),
        wp("WP-2", ["notes/**"]),
        wp("WP-3", ["tests/**"], "WP-2"),
    ),
    "markdown-html, default tiers": plan_of(
        wp("WP-1", ["md2html/**"]),
        wp("WP-2", ["tests/**"], "WP-1"),
        wp("WP-3", ["README.md"], "WP-1"),
    ),
    "url-shortener, default tiers": plan_of(
        wp("WP-1", ["shortener/__init__.py", "shortener/__main__.py", "README.md"]),
        wp("WP-2", ["shortener/server.py", "shortener/storage.py"], "WP-1"),
        wp("WP-3", ["tests/**"], "WP-2"),
    ),
}


@pytest.mark.parametrize("name", BENCHMARK_PLANS)
def test_the_rejected_benchmark_plans_now_pass_the_plan_stage(name: str) -> None:
    plan = BENCHMARK_PLANS[name]
    assert any("README.md" in problem for problem in plan_problems(plan))  # they were rejected

    repaired, changes = repair_plan(plan)

    assert plan_problems(repaired) == []
    assert changes and all("README.md" in str(change["before"]) for change in changes)
    assert not any("README.md" in p for pkg in repaired.work_packages for p in pkg.owned_paths)


def test_a_shared_file_is_taken_out_of_a_package_that_owns_other_paths_too() -> None:
    plan = BENCHMARK_PLANS["url-shortener, default tiers"]

    repaired, changes = repair_plan(plan)

    first = repaired.work_packages[0]
    assert first.owned_paths == ["shortener/__init__.py", "shortener/__main__.py"]
    assert changes == [
        {
            "package": "WP-1",
            "action": "moved",
            "before": ["shortener/__init__.py", "shortener/__main__.py", "README.md"],
            "after": ["shortener/__init__.py", "shortener/__main__.py"],
            "why": "README.md belongs to foundation and integrate",
        }
    ]


def test_a_package_left_with_nothing_is_dropped_and_its_dependents_inherit_its_dependencies() -> (
    None
):
    plan = plan_of(
        wp("A", ["a/**"]),
        wp("DOC", ["README.md"], "A"),
        wp("B", ["b/**"], "DOC"),
    )

    repaired, changes = repair_plan(plan)

    assert [p.id for p in repaired.work_packages] == ["A", "B"]
    assert repaired.work_packages[1].depends_on == ["A"]
    assert changes[0]["action"] == "dropped" and changes[0]["after"] == []
    assert plan_problems(repaired) == []


def test_a_broad_glob_is_narrowed_to_stay_out_of_the_root() -> None:
    plan = plan_of(
        wp("A", ["**/*.json", "a/**"]), wp("B", ["*.md", "docs/**"]), wp("C", ["/*.toml", "c/**"])
    )

    repaired, changes = repair_plan(plan)

    assert [p.owned_paths for p in repaired.work_packages] == [
        ["*/**/*.json", "a/**"],
        ["*/**/*.md", "docs/**"],
        ["c/**"],
    ]
    assert [c["action"] for c in changes] == ["narrowed", "narrowed", "moved"]
    assert plan_problems(repaired) == []


def test_a_glob_that_still_covers_a_shared_file_when_narrowed_is_not_repaired() -> None:
    # ``*/**`` still reaches .github/workflows/ci.yml: the architect must say what the package owns.
    plan = plan_of(wp("A", ["**"]), wp("B", ["README.md", "b/**"]))

    repaired, changes = repair_plan(plan)

    assert repaired is plan and changes == []
    assert "A owns '**'" in "\n".join(plan_problems(repaired))


def test_a_plan_that_needs_no_repair_is_returned_untouched() -> None:
    plan = plan_of(wp("A", ["a/**"]), wp("B", ["frontend/package.json", "b/**"], "A"))

    repaired, changes = repair_plan(plan)

    assert repaired is plan and changes == []


def test_shared_files_are_left_alone_when_the_stage_may_own_them() -> None:
    plan = plan_of(wp("A", ["README.md", "src/**"]))

    repaired, changes = repair_plan(plan, allow_shared=True)

    assert repaired is plan and changes == []


def test_what_cannot_be_repaired_is_still_rejected_with_its_reason() -> None:
    # Every package would be dropped: nothing is left to run, so the plan is the architect's to fix.
    plan = plan_of(wp("A", ["README.md"]), wp("B", ["/pyproject.toml", "Makefile"], "A"))

    repaired, changes = repair_plan(plan)

    assert repaired is plan and changes == []
    problems = "\n".join(plan_problems(repaired))
    assert "A owns 'README.md', which covers shared file(s) README.md" in problems


def test_other_mistakes_are_not_hidden_by_the_repair() -> None:
    spec = Spec(title="t", criteria=SPEC.criteria)
    plan = plan_of(
        wp("A", ["README.md", "a/**"], "GHOST", criteria=("AC-9",)),
        WorkPackage(id="B", title="b", role="backend", criteria_ids=["AC-1"]),
    )

    repaired, changes = repair_plan(plan)
    problems = "\n".join(plan_problems(repaired, spec))

    assert [c["package"] for c in changes] == ["A"]
    assert "A depends on unknown package 'GHOST'" in problems
    assert "A names unknown criterion 'AC-9'" in problems
    assert "B owns no paths" in problems
