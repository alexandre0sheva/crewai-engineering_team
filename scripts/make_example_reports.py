"""Regenerate the committed run reports in ``examples/*/report.html``.

Each report comes from the **offline scripted demo runner** (the one behind ``ui --demo``): the
real pipeline, task board, checks and report, with a script instead of a model. The script's
"teammates" write the benchmark suite's reference solution for the example, so the project really
goes from failing to passing, but nothing here shows what a model would write. No key is needed
and nothing is billed. Machine-specific paths are replaced by ``<workspace>``.

    uv run python scripts/make_example_reports.py            # all three
    uv run python scripts/make_example_reports.py bugfix     # one
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from engineering_team import main as engine
from engineering_team.contracts import AcceptanceCriterion, Contract, Plan, Spec, WorkPackage
from engineering_team.modes.fix_contracts import FixNote, Hypothesis, Repro, Triage
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import StageOutput, StageRequest
from engineering_team.ui import demo

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
TASKS = ROOT / "benchmarks" / "tasks"
GIT = ("git", "-c", "user.name=Example", "-c", "user.email=example@example.invalid")


def criteria(*texts: str) -> list[AcceptanceCriterion]:
    return [AcceptanceCriterion(id=f"AC-{n}", text=text) for n, text in enumerate(texts, 1)]


# What the scripted teammates do, per example: the stage that writes the reference files, the
# files they copy from the benchmark task's ``reference/`` directory, and the contracts they return.
SCRIPTS: dict[str, dict[str, object]] = {
    "greenfield-notes": {
        "task": "notes-cli",
        "spec": Spec(
            title="Notes CLI",
            summary="A command-line notes tool storing notes in a JSON file.",
            criteria=criteria(
                "add prints the new id and list shows notes oldest first with their tags",
                "ids grow by one and are never reused after a delete",
                "list --tag and search filter notes as specified",
                "delete works; unknown ids exit 1; usage errors exit 2",
            ),
        ),
        "plan": Plan(
            stack="python",
            work_packages=[
                WorkPackage(
                    id="WP-1",
                    title="Notes package",
                    role="backend",
                    owned_paths=["notes/**", "tests/**"],
                    criteria_ids=["AC-1", "AC-2", "AC-3", "AC-4"],
                )
            ],
        ),
        "write_in": "implement",
        "extra": {"tests/__init__.py": ""},  # the controller discovers tests as a package
    },
    "feature-on-legacy": {
        "task": "legacy-feature",
        "spec": Spec(
            title="Low-stock report",
            summary="GET /items/low-stock?threshold=N lists the items below N.",
            criteria=criteria(
                "the report lists items strictly below the threshold, sorted as specified",
                "threshold defaults to 10; anything but a non-negative integer is a 400",
                "the existing endpoints behave exactly as before",
            ),
        ),
        "plan": Plan(
            stack="python",
            work_packages=[
                WorkPackage(
                    id="WP-1",
                    title="Low-stock endpoint",
                    role="backend",
                    owned_paths=["inventory/**", "tests/**"],
                    criteria_ids=["AC-1", "AC-2", "AC-3"],
                )
            ],
        ),
        "write_in": "implement",
    },
    "bugfix": {
        "task": "seeded-bug",
        "triage": Triage(
            title="shop report crashes on an empty cart",
            hypotheses=[
                Hypothesis(
                    summary="Cart.average_price divides by the item count, which is 0 for [].",
                    suspects=["shop/cart.py", "shop/report.py"],
                )
            ],
        ),
        "repro": Repro(
            command="python -m unittest tests.test_empty_cart",
            files=["tests/test_empty_cart.py"],
            expected_failure="ZeroDivisionError: division by zero",
            summary="Averages an empty cart.",
        ),
        "fix_note": FixNote(
            root_cause="average_price() divided the total by a count of zero.",
            change="It returns None for an empty cart and the report prints n/a.",
            risk="Low: reports of carts with items are unchanged.",
            files=["shop/cart.py", "shop/report.py"],
        ),
        "write_in": "fix",
        "reproduce_in": "reproduce",
    },
}


class ExampleRunner(demo.DemoRunner):
    """The demo's acting (tool calls, token usage, a budget share) with an example's content."""

    name = ""

    def run(self, request: StageRequest) -> StageOutput:
        script = SCRIPTS[self.name]
        stage, package = request.stage, request.package
        reference = TASKS / str(script["task"]) / "reference"
        self._act(request, "write" if package or stage.kind == "verify" else "read", "")
        contracts: dict[str, Contract] = {}
        if stage.name == "spec" and "spec" in script:
            contracts["spec"] = script["spec"]  # type: ignore[assignment]
        elif stage.name in ("plan", "impact") and "plan" in script:
            contracts["plan"] = script["plan"]  # type: ignore[assignment]
        elif stage.name == "triage" and "triage" in script:
            contracts["triage"] = script["triage"]  # type: ignore[assignment]
        elif stage.name == "reproduce":
            self._copy(request, reference, "tests")
            contracts["repro"] = script["repro"]  # type: ignore[assignment]
        elif stage.name == "fix" and "fix_note" in script:
            self._copy(request, reference, "shop")
            contracts["fix_note"] = script["fix_note"]  # type: ignore[assignment]
        elif stage.kind == "review":
            contracts["review"] = demo.REVIEWS.get(
                request.teammate, demo.ReviewReport(summary="Clean.")
            )
        elif stage.kind == "analyze":
            contracts["analysis"] = (
                demo.CODEBASE_MAP
                if request.synthesis
                else demo.ChunkAnalysis(
                    summary="Analysed.",
                    modules=[demo.ModuleNote(name="core", path=".", purpose="The application.")],
                )
            )
        elif package is not None or stage.name == script["write_in"]:
            for sub in sorted(
                p.name for p in reference.iterdir() if p.is_dir()
            ):  # not shared files
                self._copy(request, reference, sub)
            for path, text in dict(script.get("extra") or {}).items():  # type: ignore[call-overload]
                request.ctx.workspace.write_file(path, text)
        for path in stage.file_outputs:  # what a recipe promises (only a new project's does)
            request.ctx.workspace.write_file(path, f"# {path}\n{demo.BODY}\n")
        return StageOutput(contracts=contracts, summary=f"{stage.name}: done (scripted)")

    @staticmethod
    def _copy(request: StageRequest, reference: Path, name: str) -> None:
        source = reference / name
        files = (
            [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file())
        )
        for file in files:
            if "__pycache__" in file.parts:
                continue
            relative = file.relative_to(reference).as_posix()
            request.ctx.workspace.write_file(relative, file.read_text(encoding="utf-8"))


def prepare_repo(source: Path, scratch: Path) -> Path:
    """A fresh Git copy of an example's ``repo/`` (the committed one stays untouched)."""

    repo = scratch / "repo"
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns("__pycache__"))
    for step in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "Initial")):
        subprocess.run([*GIT, *step], cwd=repo, check=True, capture_output=True)
    return repo


def arguments(name: str, scratch: Path) -> list[str]:
    example = EXAMPLES / name
    common = ["--workspace-root", str(scratch / "ws"), "--no-color"]
    request = str(example / "request.md")
    if name == "greenfield-notes":
        return ["new", "--request-file", request, "--project-name", "notes", *common]
    repo = str(prepare_repo(example / "repo", scratch))
    if name == "feature-on-legacy":
        return ["feature", "--repo", repo, "--request-file", request, *common]
    trace = str(example / "trace.txt")
    return ["fix", "--repo", repo, "--request-file", request, "--trace-file", trace, *common]


def sanitise(text: str, scratch: Path) -> str:
    for prefix in {str(scratch), str(scratch.resolve())}:
        text = text.replace(prefix, "<workspace>")
    return text.replace(str(Path.home()), "~")


def generate(name: str) -> Path:
    for variable in [v for v in os.environ if v.startswith("ENGINEERING_")]:
        del os.environ[variable]  # the conditions are the arguments below, nothing ambient
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix=f"example-{name}-") as raw:
        scratch = Path(raw).resolve()
        os.chdir(scratch)
        try:
            ExampleRunner.name, ExampleRunner.pause = name, 0.0
            demo.DemoRunner.spent = 0.0
            patched: object = strategies
            patched.CrewStageRunner = ExampleRunner  # type: ignore[attr-defined]
            patched.HierarchicalStrategy = lambda: strategies.PipelineStrategy(ExampleRunner)  # type: ignore[attr-defined]
            code = int(engine.run(arguments(name, scratch)) or 0)
        finally:
            os.chdir(previous)
        reports = sorted(scratch.rglob("report.html"), key=lambda p: p.stat().st_mtime)
        if code != 0 or not reports:
            raise SystemExit(f"{name}: the scripted run did not succeed (exit code {code}).")
        target = EXAMPLES / name / "report.html"
        text = sanitise(reports[-1].read_text(encoding="utf-8"), scratch)
        target.write_text(text, encoding="utf-8")
        return target


def main(names: list[str]) -> int:
    for name in names or list(SCRIPTS):
        print(generate(name).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
