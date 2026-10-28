"""Export a user's notes as a JSON file in the export directory, for download by the proxy."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from notes.storage import Note

EXPORT_DIR = Path(os.environ.get("NOTES_EXPORT_DIR", "/var/lib/notes/exports"))


def export_notes(notes: Iterable[Note], filename: str) -> Path:
    """Write the notes to ``EXPORT_DIR/filename`` and return the path."""

    target = EXPORT_DIR / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"id": n.id, "title": n.title, "body": n.body} for n in notes]
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target
