"""Answering the team's questions from the terminal (``--interactive``).

A background thread watches the run's :class:`~engineering_team.runtime.interaction.HumanChannel`
and prompts for each new question; an empty reply declines to answer (the asker proceeds on its
own assumption). The live screen, if there is one, is paused while a prompt is open.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager

from rich.console import Console

from engineering_team.runtime.interaction import HumanChannel, Question

POLL_SECONDS = 0.1


class TerminalAnswerer:
    """Use as ``with TerminalAnswerer(human, console): ...`` around a run."""

    def __init__(
        self,
        human: HumanChannel,
        console: Console,
        *,
        pause: Callable[[], AbstractContextManager[None]] | None = None,
        read: Callable[[str], str] | None = None,
    ) -> None:
        self._human = human
        self._console = console
        self._pause = pause or contextlib.nullcontext
        self._read = read or console.input
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="terminal-answerer", daemon=True)

    def __enter__(self) -> TerminalAnswerer:
        self._human.enable(True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)  # a prompt still open when the run ends is abandoned

    def _loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            for question in self._human.pending():
                if question.id not in self._seen:
                    self._seen.add(question.id)
                    self._prompt(question)

    def _prompt(self, question: Question) -> None:
        who = (question.agent or "the team").replace("_", " ")
        with self._pause():
            self._console.print(f"\nQuestion from {who}:", style="bold", markup=False)
            self._console.print(question.text, markup=False, highlight=False)
            try:
                reply = self._read("Your answer (Enter to let the team assume): ").strip()
            except (EOFError, KeyboardInterrupt):
                reply = ""
        if reply:
            self._human.answer(question.id, reply)
        else:
            self._human.skip(question.id)
