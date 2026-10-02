"""Deterministic project walking that skips heavy directories and honours ``.gitignore``."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from engineering_team.tools.scope import compile_glob
from engineering_team.tools.workspace import (
    CONTROLLER_DIRECTORY,
    GIT_DIRECTORY,
    IGNORED_LIST_DIRECTORIES,
    ProjectWorkspace,
)

MAX_TEXT_FILE_BYTES = 1_000_000
BINARY_SNIFF_BYTES = 8_192


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    negate: bool
    directories_only: bool


class IgnoreRules:
    """The workspace's root ``.gitignore`` (last matching rule wins, ``!`` negates).

    Nested ``.gitignore`` files are not read. Heavy directories (``node_modules``, ``.venv``,
    caches, ``build``) and the controller/Git directories are always ignored.
    """

    def __init__(self, lines: list[str] | None = None) -> None:
        self._rules: list[_Rule] = []
        for raw in lines or []:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            line = line.removeprefix("!")
            self._rules.append(
                _Rule(compile_glob(line), negate, directories_only=line.endswith("/"))
            )

    @classmethod
    def for_workspace(cls, workspace: ProjectWorkspace) -> IgnoreRules:
        try:
            text = (workspace.root / ".gitignore").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls()
        return cls(text.splitlines())

    def ignores(self, relative_path: str, *, is_dir: bool) -> bool:
        parts = relative_path.split("/")
        if any(part in IGNORED_LIST_DIRECTORIES or part == GIT_DIRECTORY for part in parts):
            return True
        if parts[0].lower() == CONTROLLER_DIRECTORY:
            return True
        ignored = False
        for rule in self._rules:
            if rule.directories_only and not is_dir:
                continue
            if rule.pattern.fullmatch(relative_path):
                ignored = not rule.negate
        return ignored


def iter_files(
    workspace: ProjectWorkspace,
    base: Path | None = None,
    *,
    rules: IgnoreRules | None = None,
) -> Iterator[Path]:
    """Yield regular files below ``base`` in sorted order, skipping ignored paths and symlinks."""

    rules = rules or IgnoreRules.for_workspace(workspace)
    start = base or workspace.root
    for current, directory_names, file_names in os.walk(start):
        here = Path(current)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not (here / name).is_symlink()
            and not rules.ignores(workspace.relative_name(here / name), is_dir=True)
        )
        for name in sorted(file_names):
            path = here / name
            if path.is_symlink() or rules.ignores(workspace.relative_name(path), is_dir=False):
                continue
            yield path


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True


def read_text_or_none(path: Path, *, limit: int = MAX_TEXT_FILE_BYTES) -> str | None:
    """The file's text, or ``None`` if it is too large, binary, or unreadable."""

    try:
        if path.stat().st_size > limit:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return None
    return data.decode("utf-8", errors="replace")
