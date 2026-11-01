"""Reflection across whole runs (T20): a run that failed and was repaired teaches a lesson, and the
next run of the same kind of work is given it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from repo_fixtures import make_repo
from test_feature_mode import git, latest
from test_fix_mode import BUGGY, FIXED, REGRESSION, fix_runner, run_fix

from engineering_team.learning import reflect
from engineering_team.learning.library import LessonLibrary, user_library
from engineering_team.learning.reflect import REFLECTION_FILE
from engineering_team.pipeline.stages import StageRequest
from engineering_team.runtime.events import read_events
from engineering_team.testing import ScriptedLLM

pytestmark = [pytest.mark.git, pytest.mark.usefixtures("full_pipeline")]

CUE = "a python bugfix where the first fix leaves the reproduction failing"


def reflection(messages: list[dict[str, Any]], tools: Any) -> str:
    """The scripted reflection: it reads the controller's record (the repair round and the
    check it was about) and proposes one lesson citing them."""

    prompt = "\n".join(str(m.get("content")) for m in messages)
    assert "verdict verified" in prompt and "Repair rounds:" in prompt
    assert "repro" in prompt and "data, not instructions" in prompt
    return json.dumps(
        {
            "rows": [
                {
                    "behaviour": "The first fix returned an empty list without running the repro.",
                    "guidance": "stages/fix",
                    "effect": "check repro failed after the fix stage; a repair round followed",
                    "verdict": "hurt",
                    "evidence": ["repro"],
                },
                {
                    "behaviour": "The repair changed add() and the repro was run again.",
                    "guidance": "stages/repair_fix",
                    "effect": "check repro passed after the repair",
                    "verdict": "helped",
                    "evidence": ["repro"],
                },
            ],
            "lesson": {
                "cue": CUE,
                "lesson": "Add to stages/fix: run the reproduction before you answer.",
                "component": "stages/fix",
                "evidence": {"rows": [1, 2], "checks": ["repro"]},
                "confidence": "medium",
            },
        }
    )


def half_then_whole(seen: list[str]) -> Any:
    """The scripted debugger: a fix that does not fix the bug, then a repair that does; it notes
    the hand-off each fix session was given."""

    def call(request: StageRequest) -> None:
        write = request.ctx.workspace.write_file
        if request.stage.name == "reproduce":
            write("tests/test_regression.py", REGRESSION)
        elif request.stage.name == "fix":
            seen.append(request.handoff)
            write("app/store.py", "def add(items, text):\n    return []\n")
        elif request.failures:
            write("app/store.py", FIXED)

    return call


def test_a_failing_then_repaired_run_teaches_a_lesson_the_next_run_receives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "notes", BUGGY)
    start = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    llm = ScriptedLLM([reflection])
    monkeypatch.setattr(reflect, "build_reflection_llm", lambda settings: llm)
    seen: list[str] = []

    first, _ = run_fix(repo, monkeypatch, fake=fix_runner(half_then_whole(seen)))

    assert first.exit_code == 0, first.output
    run = latest()
    assert run.manifest.verdict == "verified"
    assert [s.name for s in run.manifest.stages][-1] == "reflect"
    recorded = json.loads((run.run_dir / REFLECTION_FILE).read_text())
    assert recorded["outcome"] == "stored", recorded
    [learned], problems = LessonLibrary(user_library()).load()  # type: ignore[arg-type]
    assert problems == [] and learned.domain == "bugfix/python" and learned.cue == CUE
    assert learned.evidence.run_id == run.run_id and learned.evidence.check_ids == ["repro"]
    assert "lessons.stored" in {e.type for e in read_events(run.run_dir / "events.jsonl")}
    llm.assert_exhausted()
    git(repo, "checkout", "-q", start)  # back to the buggy tree for the next run

    second, _ = run_fix(repo, monkeypatch, fake=fix_runner(half_then_whole(seen)))

    assert second.exit_code == 0, second.output
    assert learned.id not in seen[0] and learned.id in seen[-1]  # the second fix was told
    assert CUE in seen[-1] and "not instructions" in seen[-1]
    events = list(read_events(latest().run_dir / "events.jsonl"))
    injected = [e for e in events if e.type == "lessons.injected"]
    assert injected and injected[0].data["lessons"] == [learned.id]
    failed = [e for e in events if e.type == "reflect.failed"]
    assert failed  # the script has no second reflection: a warning, the run still verified
    assert latest().manifest.verdict == "verified"
