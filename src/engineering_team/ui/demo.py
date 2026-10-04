"""``engineering-team ui --demo``: runs that need no API key and no model.

``python -m engineering_team.ui.demo <the usual CLI arguments>`` is the real command line with
one difference: the pipeline's stage runner is :class:`DemoRunner`, which works the way a team
does as far as the run directory can tell (tool calls by named teammates, token usage, files
written, parallel lanes) but takes its answers from a script. Everything else is the real thing:
manifest, events, task board, checks, criteria coverage, report, workspace lock, cancel and
resume. The demo only ever writes under ``engineering_team_demo/`` in the project it is given.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from engineering_team.board.rules import BoardError
from engineering_team.contracts import (
    AcceptanceCriterion,
    Contract,
    Finding,
    Plan,
    ReviewReport,
    Spec,
    WorkPackage,
)
from engineering_team.modes.codebase_map import ChunkAnalysis, CodebaseMap, ModuleNote
from engineering_team.modes.fix_contracts import FixNote, Hypothesis, Repro, Triage
from engineering_team.modes.maintain_contracts import UpgradePlan
from engineering_team.modes.repo_analyzer import standalone_git
from engineering_team.pipeline import strategies
from engineering_team.pipeline.stages import StageOutput, StageRequest

DEMO_DIR = "engineering_team_demo"
DEMO_MODEL = "openai/gpt-6.1-sol"
DEMO_BUDGET_USD = 1.0  # the cost limit ``ui --demo`` sets, so the run shows a budget warning
TARGET_SPEND = 0.82  # what the demo tops its spending up to (82% of the limit)
ASK_TIMEOUT = 90.0  # seconds the demo waits for a person to answer its question
FINAL_STAGES = ("review", "release", "build")
PAUSE = 0.35  # seconds between a teammate's tool calls (``--demo-pause`` changes it)
TOOLS: dict[str, Sequence[tuple[str, str]]] = {
    "read": (("List Files", "."), ("Read File", "README.md"), ("Search Text", "def main")),
    "write": (
        ("List Files", "."),
        ("Search Text", "def "),
        ("Read File", "{path}"),
        ("Write File", "{path}"),
        ("Run Command", "pytest -q"),
    ),
}
MODULES = {
    "Storage": "NOTES: list[str] = []\n\n\ndef add(text: str) -> int:\n"
    '    """Store a note and return how many notes there are."""\n'
    "    NOTES.append(text.strip())\n    return len(NOTES)\n",
    "Listing": "from engineering_team_demo.storage import NOTES\n\n\ndef listing() -> list[str]:\n"
    '    """Every stored note, oldest first."""\n    return list(NOTES)\n',
    "Command line": "import sys\n\n\ndef main(argv: list[str]) -> int:\n"
    '    if not argv:\n        print("usage: notes add TEXT | list")\n        return 2\n'
    "    return 0\n\n\nif __name__ == '__main__':\n    sys.exit(main(sys.argv[1:]))\n",
}
TESTS = (
    "from engineering_team_demo.storage import add\n\n\n"
    "def test_ac_1_add_stores_a_note() -> None:  # AC-1\n    assert add(' hello ') >= 1\n\n\n"
    "def test_ac_2_listing_returns_notes() -> None:  # AC-2\n    assert add('x') >= 1\n"
)
TESTS_BROKEN = TESTS.replace("assert add('x') >= 1", "assert add('x') == 99  # not yet true")
BODY = "Written by the demo runner; no model was called. " * 3

SPEC = Spec(
    title="Notes helper (demo)",
    summary="A small notes module with storage and a command line.",
    criteria=[
        AcceptanceCriterion(id="AC-1", text="A note can be added and stored."),
        AcceptanceCriterion(id="AC-2", text="Stored notes can be listed."),
        AcceptanceCriterion(id="AC-3", text="The command line prints a usage message."),
    ],
)
PLAN = Plan(
    stack="python",
    work_packages=[
        WorkPackage(
            id="WP-1",
            title="Storage",
            role="backend",
            owned_paths=[f"{DEMO_DIR}/storage.py"],
            criteria_ids=["AC-1"],
        ),
        WorkPackage(
            id="WP-2",
            title="Listing",
            role="backend",
            owned_paths=[f"{DEMO_DIR}/listing.py"],
            criteria_ids=["AC-2"],
        ),
        WorkPackage(
            id="WP-3",
            title="Command line",
            role="frontend",
            owned_paths=[f"{DEMO_DIR}/command_line.py"],
            criteria_ids=["AC-3"],
        ),
    ],
)
CODEBASE_MAP = CodebaseMap(
    overview="A small application (demo analysis).",
    architecture="One package with a command line over a storage module.",
    modules=[ModuleNote(name="core", path="src", purpose="The application code.")],
    key_flows=["The entry point parses arguments and calls the storage module."],
    conventions=["Plain functions."],
    hotspots=["The storage module changes most."],
    risks=["Few tests."],
    how_to_run=["See the README."],
    how_to_test=["See the README."],
)
REVIEWS = {
    "security_engineer": ReviewReport(
        summary="Nothing dangerous; one hardening suggestion.",
        findings=[
            Finding(
                severity="medium",
                summary="Notes are written without checking the path stays inside the project.",
                file=f"{DEMO_DIR}/storage.py",
                suggested_fix="Resolve the path and compare it with the project root.",
            )
        ],
    ),
    "code_reviewer": ReviewReport(
        summary="Readable; two small things.",
        findings=[
            Finding(
                severity="low",
                summary="add() returns a count the callers never use.",
                file=f"{DEMO_DIR}/storage.py",
                line=6,
            ),
            Finding(severity="info", summary="Consider a docstring for the listing module."),
        ],
    ),
}


class DemoRunner:
    """A :class:`StageRunner` that acts out the stage and returns scripted contracts."""

    pause = PAUSE
    spent = 0.0  # what this process has "spent" so far, in USD
    guard = threading.Lock()  # parallel lanes share ``spent``

    def run(self, request: StageRequest) -> StageOutput:
        ctx, stage, package = request.ctx, request.stage, request.package
        path = f"{DEMO_DIR}/{package.title.lower().replace(' ', '_')}.py" if package else ""
        self._act(request, "write" if package or stage.kind == "verify" else "read", path)
        if request.steering:  # a note from the person, delivered once (see the board)
            ctx.events.emit(
                "tool.call", tool="Read Steering", args={"note": request.steering},
                duration=0.0, ok=True, agent=request.teammate, lane=request.lane,
            )  # fmt: skip
        notes = "plain text"
        if package is not None and package.id == "WP-2":
            notes = self._ask(request) or notes
        write = ctx.workspace.write_file
        contracts: dict[str, Contract] = {}
        name = stage.name
        if stage.kind == "analyze":
            contracts["analysis"] = (
                CODEBASE_MAP
                if request.synthesis
                else ChunkAnalysis(
                    summary="Analysed (demo).",
                    modules=[ModuleNote(name="core", path="src", purpose="The core.")],
                )
            )
        elif name == "spec":
            contracts["spec"] = SPEC
        elif name in ("plan", "impact"):
            contracts["plan"] = PLAN
        elif stage.kind == "review":
            contracts["review"] = REVIEWS.get(request.teammate, ReviewReport(summary="Clean."))
        elif name == "plan_upgrades":
            contracts["upgrades"] = UpgradePlan()
        elif name == "triage":
            contracts["triage"] = Triage(
                title="Demo bug",
                hypotheses=[Hypothesis(summary="The cause is in the storage module.")],
            )
        elif name == "reproduce":
            contracts["repro"] = Repro()
        elif name == "fix":
            contracts["fix_note"] = FixNote(
                root_cause="Demo root cause.", change="Demo change.", risk="Low."
            )
        elif package is not None:
            header = f'"""{package.title} (written by the demo runner; notes are {notes})."""\n\n'
            write(path, header + MODULES[package.title])
        elif name == "tests" or name == "integrate":
            write(f"{DEMO_DIR}/__init__.py", "")
            write(f"{DEMO_DIR}/test_notes.py", TESTS_BROKEN)  # a check fails; verify repairs it
        elif stage.kind == "verify":
            write(f"{DEMO_DIR}/test_notes.py", TESTS)  # the repair
        for path in stage.file_outputs:  # what a recipe promises (only a new project's does)
            write(path, f"# {path}\n{BODY}\n")
        return StageOutput(contracts=contracts, summary=f"{name}: done (demo)")

    def _act(self, request: StageRequest, kind: str, path: str) -> None:
        ctx = request.ctx
        agent = request.teammate
        for tool, argument in TOOLS[kind]:
            ctx.board.wait_while_paused(ctx.cancel_event)
            if ctx.cancel_event.is_set():
                return
            time.sleep(self.pause)
            ctx.events.emit(
                "tool.call",
                tool=tool,
                args={"target": argument.format(path=path)},
                duration=round(self.pause, 3),
                ok=True,
                agent=agent,
                lane=request.lane,
            )
        self._spend(request, 9_000, 1_500)
        if request.stage.name in FINAL_STAGES:
            self._top_up(request)

    def _spend(self, request: StageRequest, prompt: int, completion: int) -> None:
        ctx = request.ctx
        usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        ctx.events.emit("llm.call", agent=request.teammate, model=DEMO_MODEL, usage=usage)
        price = ctx.settings.price_table().lookup(DEMO_MODEL)
        if price is not None:
            with DemoRunner.guard:
                DemoRunner.spent += (prompt * price.input + completion * price.output) / 1e6

    def _top_up(self, request: StageRequest) -> None:
        """One long, expensive call in the last stage, so every run ends at the same share of the
        demo's cost limit and shows the budget warning whatever its mode."""

        price = request.ctx.settings.price_table().lookup(DEMO_MODEL)
        if price is None or price.output <= 0:
            return
        with DemoRunner.guard:
            missing = TARGET_SPEND * DEMO_BUDGET_USD - DemoRunner.spent
            DemoRunner.spent += max(missing, 0.0)  # claimed now, so no other lane tops up too
        if missing > 0:
            usage = {"prompt_tokens": 0, "completion_tokens": int(missing / price.output * 1e6)}
            ctx = request.ctx
            ctx.events.emit("llm.call", agent=request.teammate, model=DEMO_MODEL, usage=usage)

    def _ask(self, request: StageRequest) -> str | None:
        """Block the card, ask the person, and carry on with the answer (or an assumption)."""

        ctx, agent, card = request.ctx, request.teammate, request.card_id
        if card:
            with contextlib.suppress(BoardError):
                ctx.board.move(
                    card, "blocked", actor=agent, reason="Waiting for the person's answer"
                )
        answer = ctx.human.ask(
            "Should the notes be stored as plain text or as Markdown?",
            agent=agent,
            card_id=card,
            timeout=ASK_TIMEOUT,
            cancel_event=ctx.cancel_event,
        )
        if card:
            with contextlib.suppress(BoardError):
                ctx.board.move(card, "in_progress", actor=agent)
        return answer.strip() or None if answer else None


SAMPLE_FILES = {
    "README.md": "# Sample project\n\nA tiny project for trying the web UI in demo mode.\n",
    "app/__init__.py": "",
    "app/main.py": "def main() -> None:\n    print('hello')\n",
    "tests/test_main.py": "def test_ok() -> None:\n    assert True\n",
    "pyproject.toml": "[project]\nname = 'sample'\nversion = '0.1.0'\n",
}


def ensure_sample_repo(parent: Path) -> Path:
    """A small Git project under ``parent`` the demo can work on (made once, then reused)."""

    repo = parent / "sample-project"
    if (repo / ".git").is_dir():
        return repo
    for name, text in SAMPLE_FILES.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    with standalone_git(repo) as git:
        git.init()
        git.checkpoint("Sample project")
    return repo


def main(argv: Sequence[str] | None = None) -> int:
    """The CLI with the demo runner in place of the model-driven one. ``--demo-pause SECONDS``
    (first) sets the time between tool calls."""

    from engineering_team import main as engine

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--demo-pause"] and len(args) > 1:
        DemoRunner.pause = float(args[1])
        args = args[2:]
    patched: Any = strategies
    patched.CrewStageRunner = DemoRunner
    patched.HierarchicalStrategy = lambda: strategies.PipelineStrategy(DemoRunner)
    return int(engine.run(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
