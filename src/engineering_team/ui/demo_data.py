"""What the demo scripts besides the pipeline's stages: the answer to a question about the sample
project, and the lessons in the demo's own experience library (``ui/demo.py`` runs the stages)."""

from __future__ import annotations

from pathlib import Path

from engineering_team.learning.lessons import BehaviourRow, Lesson, LessonEvidence, lesson_id
from engineering_team.learning.library import LessonLibrary
from engineering_team.modes.answer_contracts import Answer, AnswerSection, Citation

ANSWER = Answer(
    summary="`main` in app/main.py is the entry point; it only prints a greeting [1], and the "
    "project's README says it is a small sample [2].",
    sections=[
        AnswerSection(
            heading="How it works",
            body="Running the package calls `main()`, which prints `hello` and returns [1]. "
            "Nothing else is wired in yet.",
        ),
        AnswerSection(
            heading="What it is for",
            body="The README describes the project as a tiny project for trying the web UI [2].",
        ),
    ],
    citations=[
        Citation(path="app/main.py", lines="1-2", quote="def main() -> None:"),
        Citation(path="README.md", lines="1", quote="# Sample project"),
    ],
    confidence="high",
    open_questions=["Should `main` read its arguments from the command line?"],
)

DEMO_LESSONS = (
    (
        "a feature adds a search or list command to a small Python application",
        "Add to stages/implement: put the new command beside the existing ones and write its test "
        "in the same change, so the controller's check has something to run.",
        "stages/implement",
        "high",
        False,
        "helped",
    ),
    (
        "a failing test names a module the change did not touch",
        "Add to skills/pytest-failures: read the failing test's imports before editing the "
        "module under test.",
        "skills/pytest-failures",
        "medium",
        True,
        "helped",
    ),
    (
        "the request names a command line flag that the project does not have yet",
        "Add to stages/design: list the flag in the spec's acceptance criteria before building.",
        "stages/design",
        "low",
        True,
        "confounded",
    ),
)


def seed_learning(library_root: Path) -> None:
    """A few lessons for the demo's experience library, so the learning screens have something
    to show: one that later runs receive and two waiting for approval (the demo's runs call no
    model, so none is distilled from them)."""

    library = LessonLibrary(library_root)
    for cue, text, component, confidence, pending, verdict in DEMO_LESSONS:
        domain = "feature/python"
        library.add(
            Lesson(
                id=lesson_id(domain, cue, text),
                domain=domain,
                cue=cue,
                lesson=text,
                component=component,
                confidence=confidence,  # type: ignore[arg-type]
                evidence=LessonEvidence(
                    run_id="demo-seed",
                    verdict="verified",
                    check_ids=["tests"],
                    rows=[
                        BehaviourRow(
                            behaviour="The agent departed from the guidance.",
                            guidance=component,
                            effect="The controller's check passed after the repair.",
                            verdict=verdict,  # type: ignore[arg-type]
                            evidence=["tests"],
                        )
                    ],
                ),
            ),
            pending=pending,
        )
