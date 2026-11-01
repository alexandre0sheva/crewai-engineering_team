"""What keeps a lesson out of the library: secrets, and text of protected files.

A lesson is written by a model from a run's records, so it may quote what it read. Two guards run
before anything is stored: the secret scan of repo memory (a secret value of this environment, or
the shape of a credential), and the **leak guard**: no lesson may contain text of the protected
files (``learning.protected_paths``; a benchmark run names its task's hidden checks and reference
solution there), so hidden checks can never reach a library, and from there a prompt.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from engineering_team.memory.facts import secret_in

MIN_LINE_CHARS = 20  # a protected line shorter than this is too common to mean anything
SHINGLE_WORDS = 8  # this many words in a row, in the same order, count as a quotation
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 500
_WORD = re.compile(r"[A-Za-z0-9_]+")


def _normal(text: str) -> str:
    return " ".join(text.lower().split())


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


class Protected:
    """The text of the protected files, as lines and word shingles."""

    def __init__(self, texts: Iterable[str] = ()) -> None:
        self.lines: set[str] = set()
        self.shingles: set[tuple[str, ...]] = set()
        for text in texts:
            self.add(text)

    def add(self, text: str) -> None:
        for line in text.splitlines():
            normal = _normal(line)
            if len(normal) >= MIN_LINE_CHARS:
                self.lines.add(normal)
        self.shingles |= _shingles(text)

    def __bool__(self) -> bool:
        return bool(self.lines or self.shingles)

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> Protected:
        """The text of every readable file at or under ``paths`` (capped)."""

        protected = cls()
        files: list[Path] = []
        for name in paths:
            path = Path(name).expanduser()
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
        for path in files[:MAX_FILES]:
            try:
                if path.stat().st_size <= MAX_FILE_BYTES:
                    protected.add(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
        return protected


def leak_in(text: str, protected: Protected) -> str | None:
    """Why ``text`` quotes a protected file (``None`` if it does not)."""

    if not protected:
        return None
    normal = _normal(text)
    if any(line in normal for line in protected.lines):
        return "it quotes a line of a protected file (a benchmark's hidden checks or reference)"
    if _shingles(text) & protected.shingles:
        return "it quotes a passage of a protected file (a benchmark's hidden checks or reference)"
    return None


def guard(texts: Iterable[str], protected: Protected, secrets: tuple[str, ...] = ()) -> str | None:
    """Why these texts of one lesson cannot be stored (``None`` when they can)."""

    for text in texts:
        reason = secret_in(text, secrets) or leak_in(text, protected)
        if reason is not None:
            return reason
    return None
