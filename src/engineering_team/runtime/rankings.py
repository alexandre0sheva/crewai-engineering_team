"""The run's file ranking, kept while it still fits (``pipeline/context_pack.py``).

Ranking reads every source file of the workspace, so a run does it once per request and set of
files, not once per stage session: parallel lanes that start together share one result.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class RankingCache:
    """One cached value under a key; a different key builds again. Safe across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: str | None = None
        self._value: Any = None

    def get(self, key: str, build: Callable[[], Any]) -> Any:
        with self._lock:
            if self._key != key:
                self._value = build()
                self._key = key
            return self._value
