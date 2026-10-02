"""Crash-safe file writes, shared by the run store and the task board."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` so readers see the old file or the new one, never a partial one.

    The data goes to a temp file in the same directory, is flushed to disk, and replaces the
    target with ``os.replace`` (atomic on POSIX).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")
