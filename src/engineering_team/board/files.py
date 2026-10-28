"""Where the board lives on disk: ``board.json`` is the record, ``board.md`` a view."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from engineering_team.atomic_io import atomic_write_json, atomic_write_text
from engineering_team.board.models import (
    BoardState,
)
from engineering_team.board.progress import compute_progress
from engineering_team.board.render import render_board

log = logging.getLogger(__name__)


class BoardFiles:
    """Saving, rendering, and loading the board; mixed into :class:`BoardStore`, which holds
    the state and the lock these methods work under."""

    path: Path
    markdown_path: Path
    _state: BoardState
    _lock: threading.RLock
    _timer: threading.Timer | None
    _render_delay: float
    _clock: Callable[[], datetime]

    # -- files -------------------------------------------------------------------------

    def flush(self) -> None:
        """Write ``board.json`` and ``board.md`` now (the Markdown is otherwise debounced)."""

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._save()
            self._render()

    def markdown(self) -> str:
        with self._lock:
            return render_board(self._state, compute_progress(self._state.cards, self._clock()))

    def _changed(self) -> None:
        self._state.updated = self._clock()
        self._save()
        if self._render_delay <= 0:
            self._render()
        elif self._timer is None:
            self._timer = threading.Timer(self._render_delay, self._render_later)
            self._timer.daemon = True
            self._timer.start()

    def _render_later(self) -> None:
        with self._lock:
            self._timer = None
            self._render()

    def _save(self) -> None:
        try:
            atomic_write_json(self.path, self._state.model_dump(mode="json"))
        except OSError:
            log.warning("Could not write %s", self.path, exc_info=True)

    def _render(self) -> None:
        try:
            atomic_write_text(self.markdown_path, self.markdown())
        except OSError:
            log.warning("Could not write %s", self.markdown_path, exc_info=True)

    def _load(self, run_id: str) -> BoardState:
        try:
            return BoardState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BoardState(run_id=run_id)
        except (OSError, ValueError):
            log.warning("Ignoring unreadable %s; starting an empty board", self.path, exc_info=True)
            return BoardState(run_id=run_id)
