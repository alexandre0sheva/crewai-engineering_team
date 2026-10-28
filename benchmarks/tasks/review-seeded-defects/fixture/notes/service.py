"""What a signed-in user may do with notes. Users only ever see and change their own notes."""

from __future__ import annotations

from notes.storage import Note, NoteStore


class NotFound(LookupError):
    """No such note for this user (also used for other users' notes: no existence leak)."""


class NoteService:
    def __init__(self, store: NoteStore) -> None:
        self.store = store

    def create(self, user: str, title: str, body: str = "") -> Note:
        if not title.strip():
            raise ValueError("title must not be empty")
        return self.store.add(user, title.strip(), body)

    def read(self, user: str, note_id: int) -> Note:
        note = self.store.get(note_id)
        if note is None or note.owner != user:
            raise NotFound(note_id)
        return note

    def mine(self, user: str) -> list[Note]:
        return self.store.for_owner(user)
