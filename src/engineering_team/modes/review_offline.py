"""``review --fake``: the reviewers on a scripted model, so a pipeline can exercise the whole
review (diff, findings files, SARIF, comment, exit code) with no key and no spend.

The "model" answers every reviewer with an empty report, so a fake review is always clean: it
proves the plumbing, never the change. Nothing leaves the machine.
"""

from __future__ import annotations

from typing import Any

from engineering_team.contracts import ReviewReport
from engineering_team.pipeline.stages import CrewStageRunner

FAKE_MODEL = "ollama/qwen3.8:27b"  # a priced (free) model name, so the cost reads $0.00
FAKE_SUMMARY = "Offline review (--fake): no model was called, so nothing was examined."
REPLIES_PER_AGENT = 4  # a reviewer needs one answer; the rest is slack for a format retry


def _model(_teammate: str) -> Any:
    from engineering_team.testing import ScriptedLLM

    answer = ReviewReport(summary=FAKE_SUMMARY).model_dump_json(exclude={"schema_version"})
    return ScriptedLLM([answer] * REPLIES_PER_AGENT, model=FAKE_MODEL)


def offline_runner() -> CrewStageRunner:
    """A stage runner whose every agent talks to the scripted model."""

    return CrewStageRunner(llm_factory=_model)
