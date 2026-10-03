"""Fakes for pipeline tests: a stage runner that needs no model, and helpers to drive runs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from engineering_team.contracts import (
    AcceptanceCriterion,
    Contract,
    Plan,
    ReviewReport,
    Spec,
    WorkPackage,
)
from engineering_team.modes.codebase_map import ChunkAnalysis, CodebaseMap, ModuleNote
from engineering_team.pipeline.stages import StageOutput, StageRequest
from engineering_team.settings import Settings, load_settings

REQUEST = "Build a tiny notes CLI with add and list commands."

SPEC = Spec(
    title="Notes CLI",
    summary="Add and list notes.",
    criteria=[
        AcceptanceCriterion(id="AC-1", text="add stores a note"),
        AcceptanceCriterion(id="AC-2", text="list prints notes"),
    ],
)

PLAN = Plan(
    stack="python",
    work_packages=[
        WorkPackage(
            id="WP-1",
            title="Storage",
            role="backend",
            owned_paths=["src/storage/**"],
            criteria_ids=["AC-1"],
        ),
        WorkPackage(
            id="WP-2",
            title="CLI",
            role="backend",
            owned_paths=["src/cli/**"],
            depends_on=["WP-1"],
            criteria_ids=["AC-2"],
        ),
    ],
)

CODEBASE_MAP = CodebaseMap(
    overview="A small notes application.",
    architecture="A CLI over a storage module.",
    modules=[ModuleNote(name="store", path="app/store.py", purpose="Keeps notes.")],
    key_flows=["main parses the arguments and calls store.add."],
    conventions=["Plain functions, no classes."],
    hotspots=["app/store.py changes most."],
    risks=["Few tests."],
    how_to_run=["python -m app.main TEXT"],
    how_to_test=["make test"],
)

# What the agent stages after ``verify`` call, in order: two reviewers (side by side, so their
# order is not fixed, and both are ("review", None)), DevOps, the writer, then the release.
TAIL: list[tuple[str, str | None]] = [
    ("review", None), ("review", None), ("devops", None), ("docs", None), ("release", None),
]  # fmt: skip

PASSING_CHECKS = "- {id: tests, name: Project tests, kind: test, command: 'true'}\n"


def write_checks(text: str = PASSING_CHECKS, name: str = "checks.yaml") -> Path:
    """A user checks file outside any project (the sandbox working directory)."""

    path = Path.cwd() / name
    path.write_text(text, encoding="utf-8")
    return path


STAGES = (
    "spec", "plan", "foundation", "implement", "integrate", "verify", "review", "devops", "docs",
    "release",
)  # fmt: skip
BODY = "A concrete sentence that is long enough to count as real content. " * 2


def settings_for(root: Path, **overrides: object) -> Settings:
    """Settings for a project named ``demo`` under ``root`` using the pipeline strategy."""

    values = {
        "project_name": "demo",
        "workspace_root": str(root),
        "strategy": "pipeline",
        "verbose": False,
    }
    values.update(overrides)
    return load_settings().with_overrides(values, source="test")


class FakeRunner:
    """A stage runner that writes deterministic files and returns scripted contracts.

    ``fail`` maps a stage (or ``stage:package``) to how many times it should fail first;
    ``cancel_at`` makes that stage ask the run to cancel; ``plan`` is what the plan stage returns;
    ``specs`` are what successive spec-stage calls return (the last repeats).
    The ``verify`` stage is run by the controller; the runner is only called for it to repair
    failures (its request then carries ``failures``). ``calls`` lists every ``(stage, package id
    or None)`` it was asked to run, in order.
    """

    def __init__(
        self,
        *,
        plan: Plan | None = None,
        fail: dict[str, int] | None = None,
        cancel_at: str | None = None,
        on_call: Callable[[StageRequest], None] | None = None,
        says: dict[str, str] | None = None,
        specs: list[Spec] | None = None,
        reviews: dict[str, ReviewReport] | None = None,
        codebase_map: CodebaseMap | None = None,
    ) -> None:
        self.codebase_map = codebase_map or CODEBASE_MAP  # what an ``analyze`` synthesis returns
        self.plan = PLAN if plan is None else plan
        self.reviews = dict(reviews or {})  # teammate -> the report that reviewer returns
        self.specs = list(specs or [SPEC])  # what the spec stage returns; the last one repeats
        self.fail = dict(fail or {})
        self.cancel_at = cancel_at
        self.on_call = on_call
        self.says = dict(says or {})  # what a stage's agent reports instead of "<stage> done"
        self.calls: list[tuple[str, str | None]] = []
        self.requests: list[StageRequest] = []

    def run(self, request: StageRequest) -> StageOutput:
        stage, package = request.stage.name, request.package
        key = f"{stage}:{package.id}" if package else stage
        self.calls.append((stage, package.id if package else None))
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call(request)
        if self.cancel_at == stage:
            self.cancel_at = None
            request.ctx.cancel_event.set()
        for name in (key, stage):
            if self.fail.get(name, 0) > 0:
                self.fail[name] -= 1
                raise RuntimeError(f"scripted failure in {key}")
        write = request.ctx.workspace.write_file
        contracts: dict[str, Contract] = {}
        if request.stage.kind == "analyze":  # chunk analysts, then the synthesis
            contracts["analysis"] = (
                self.codebase_map
                if request.synthesis
                else ChunkAnalysis(
                    summary=f"Analysed. {request.chunk.splitlines()[0]}",
                    modules=[ModuleNote(name="core", path="src", purpose="The core.")],
                )
            )
        elif stage == "spec":
            contracts["spec"] = self.specs.pop(0) if len(self.specs) > 1 else self.specs[0]
        elif stage == "plan":
            write("docs/architecture.md", "# Architecture\n" + BODY)
            contracts["plan"] = self.plan
        elif stage == "foundation":
            write("README.md", "# Demo\n" + BODY)
        elif package is not None:
            write(f"src/{package.id.lower()}.py", f"# {package.title}\nVALUE = 1\n")
        elif stage == "integrate":
            write("docs/integration.md", "# Integration\n" + BODY)
        elif stage == "review":
            contracts["review"] = self.reviews.get(request.teammate, ReviewReport(summary="Clean."))
        elif stage == "devops":
            write("docs/devops.md", "# DevOps\n" + BODY)
        elif stage == "docs":
            write("docs/usage.md", "# Usage\n" + BODY)
        elif stage == "release":
            write("docs/release-report.md", "# Release\n" + BODY)
        elif stage == "build":  # the single-agent strategy's one stage
            write("README.md", "# Demo\n" + BODY)
            write("docs/release-report.md", "# Release\n" + BODY)
        return StageOutput(contracts=contracts, summary=self.says.get(key, f"{key} done"))
