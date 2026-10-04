"""Notes kept in one JSON file."""

import json
import os
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("NOTES_FILE") or "notes.json")


def load() -> dict:
    try:
        return json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"next_id": 1, "notes": []}


def save(data: dict) -> None:
    path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def add(text: str, tags: list[str]) -> dict:
    data = load()
    note = {"id": data["next_id"], "text": text, "tags": tags}
    data["next_id"] += 1
    data["notes"].append(note)
    save(data)
    return note


def remove(note_id: int) -> bool:
    data = load()
    kept = [n for n in data["notes"] if n["id"] != note_id]
    if len(kept) == len(data["notes"]):
        return False
    data["notes"] = kept
    save(data)
    return True


def find(tag: str | None = None, word: str | None = None) -> list[dict]:
    found = load()["notes"]
    if tag is not None:
        found = [n for n in found if tag in n["tags"]]
    if word is not None:
        found = [n for n in found if word.lower() in n["text"].lower()]
    return found
