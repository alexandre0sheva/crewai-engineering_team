"""A run that stops to ask a person something instead of guessing (exit code 4, ``needs-info``)."""

from __future__ import annotations


class NeedsInfo(RuntimeError):
    """The run cannot go on without an answer: nothing was changed blind. ``questions`` are what
    to ask; the pipeline records them in its state and ends the run ``needs-info``."""

    def __init__(self, message: str, questions: list[str]) -> None:
        super().__init__(message)
        self.questions = questions
