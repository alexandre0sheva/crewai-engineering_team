"""Shared notes between agents, and the project's append-only decision log.

Notes live in ``run_dir/notes/<key>.md`` and belong to one run. The decision log is
``<workspace>/.engineering-team/decisions.md``: it outlives runs, only grows (until its size
cap), and agents may cite it. Both are controller-owned files agents reach only through the
note tools. Their content is written by agents, so it is data for other agents, never policy.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from engineering_team.atomic_io import atomic_write_text
from engineering_team.contracts import utc_now
from engineering_team.runtime.events import EventSink, NullSink

DECISIONS_KEY = "decisions"
MAX_NOTE_CHARS = 20_000
MAX_NOTES = 200
MAX_DECISION_CHARS = 1_000
MAX_DECISIONS_FILE_CHARS = 50_000
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class NoteError(ValueError):
    """A note operation that is not allowed; the message says how to fix the call."""


class NoteStore:
    """Reads and writes one run's notes and the project's decision log; thread-safe."""

    def __init__(
        self,
        notes_dir: Path,
        decisions_path: Path,
        events: EventSink | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.notes_dir = notes_dir
        self.decisions_path = decisions_path
        self._events = events or NullSink()
        self._clock = clock
        self._lock = threading.RLock()

    def write(self, key: str, text: str, *, author: str, append: bool = False) -> int:
        """Replace (or append to) a note; returns the note's size in characters."""

        path = self._path(key)
        if not text.strip():
            raise NoteError("The note text is empty.")
        with self._lock:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            if not existing and len(self._keys()) >= MAX_NOTES:
                raise NoteError(
                    f"There are already {MAX_NOTES} notes; reuse a key from List Notes."
                )
            body = f"{existing.rstrip()}\n\n{text.strip()}\n" if append and existing else text
            if len(body) > MAX_NOTE_CHARS:
                raise NoteError(
                    f"Note {key!r} would be {len(body)} characters; the limit is {MAX_NOTE_CHARS}."
                    " Summarize it, or split it under another key."
                )
            atomic_write_text(path, body)
        self._events.emit("note.written", key=key, chars=len(body), author=author, append=append)
        return len(body)

    def read(self, key: str) -> str:
        """A note's text; ``decisions`` reads the project's decision log."""

        path = self.decisions_path if key == DECISIONS_KEY else self._path(key)
        with self._lock:
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError:
                if key == DECISIONS_KEY:
                    return ""
                known = ", ".join(name for name, _ in self.entries()[:8]) or "none yet"
                raise NoteError(f"No note {key!r}. Known notes: {known}.") from None

    def entries(self) -> list[tuple[str, int]]:
        """``(key, characters)`` for every note, sorted by key."""

        with self._lock:
            return [
                (key, len((self.notes_dir / f"{key}.md").read_text(encoding="utf-8")))
                for key in self._keys()
            ]

    def log_decision(self, text: str, *, author: str) -> int:
        """Append one dated line to the decision log; returns the log's size in characters."""

        text = " ".join(text.split())
        if not text:
            raise NoteError("The decision text is empty.")
        if len(text) > MAX_DECISION_CHARS:
            raise NoteError(
                f"The decision is {len(text)} characters; the limit is {MAX_DECISION_CHARS}."
                " State the decision in a sentence or two."
            )
        line = f"- {self._clock():%Y-%m-%d %H:%M}Z ({author}) {text}\n"
        with self._lock:
            existing = (
                self.decisions_path.read_text(encoding="utf-8")
                if self.decisions_path.is_file()
                else "# Decisions\n\n"
            )
            if len(existing) + len(line) > MAX_DECISIONS_FILE_CHARS:
                raise NoteError(
                    f"The decision log is full ({MAX_DECISIONS_FILE_CHARS} characters); "
                    "it only grows, so ask the human to archive it."
                )
            atomic_write_text(self.decisions_path, existing + line)
        self._events.emit("decision.logged", author=author, chars=len(text))
        return len(existing) + len(line)

    def _path(self, key: str) -> Path:
        if key == DECISIONS_KEY:
            raise NoteError("'decisions' is the decision log: add to it with Log Decision.")
        if not KEY_PATTERN.fullmatch(key):
            raise NoteError(
                f"Invalid note key {key!r}: use 1-64 letters, digits, '.', '_' or '-' "
                "(for example 'api-contract')."
            )
        return self.notes_dir / f"{key}.md"

    def _keys(self) -> list[str]:
        if not self.notes_dir.is_dir():
            return []
        return sorted(path.stem for path in self.notes_dir.glob("*.md") if path.is_file())
