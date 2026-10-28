"""SQLite storage for notes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True)
class Note:
    id: int
    owner: str
    title: str
    body: str


class NoteStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.db = sqlite3.connect(str(path))
        self.db.execute(SCHEMA)

    def add(self, owner: str, title: str, body: str = "") -> Note:
        cursor = self.db.execute(
            "INSERT INTO notes (owner, title, body) VALUES (?, ?, ?)", (owner, title, body)
        )
        self.db.commit()
        return Note(int(cursor.lastrowid or 0), owner, title, body)

    def get(self, note_id: int) -> Note | None:
        row = self.db.execute(
            "SELECT id, owner, title, body FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return Note(*row) if row else None

    def for_owner(self, owner: str, page: int = 1, size: int = 20) -> list[Note]:
        """One page (numbered from 1) of the owner's notes, oldest first."""

        # One row more than a page, to tell whether there is a next page.
        rows = self.db.execute(
            "SELECT id, owner, title, body FROM notes WHERE owner = ? ORDER BY id LIMIT ? OFFSET ?",
            (owner, size + 1, (page - 1) * size),
        )
        return [Note(*row) for row in rows]

    def search(self, owner: str, term: str) -> list[Note]:
        """The owner's notes whose title or body contains ``term``."""

        rows = self.db.execute(
            "SELECT id, owner, title, body FROM notes "
            f"WHERE owner = ? AND (title LIKE '%{term}%' OR body LIKE '%{term}%') ORDER BY id",
            (owner,),
        )
        return [Note(*row) for row in rows]

    def delete(self, note_id: int) -> None:
        self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.db.commit()
