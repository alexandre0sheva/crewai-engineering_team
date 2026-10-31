"""Skills: knowledge a teammate loads when it needs it (docs/HARNESS.md#skills).

The Anthropic Agent Skills layout: ``skills/<name>/SKILL.md`` with ``name`` and ``description``
front-matter (the description says when to use it: it is the trigger) and the instructions as the
body, plus optional files beside it (``reference/*.md`` and the like). Only the name and the
description are always on; the ``Load Skill`` tool returns the body, or one other file, when the
teammate asks. Nothing in a skill is ever executed: a script is text. ``teammates`` (our own
field) lists the teammates that see the skill; empty means every teammate with the ``knowledge``
tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from typing import Any

from engineering_team.harness.files import read, split_front_matter, unknown_fields
from engineering_team.settings import TEAM_KEY

SKILL_FILE = "SKILL.md"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME = 64
MAX_DESCRIPTION = 120  # it is always on: one short trigger
MAX_FILE_CHARS = 12_000
MAX_FILES = 40
FIELDS = ("name", "description", "teammates", "license", "allowed-tools", "metadata")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    teammates: tuple[str, ...]
    pack: str
    source: str  # bundled, user or repository
    trusted: bool
    folder: Traversable
    where: str

    def visible_to(self, key: str) -> bool:
        return not self.teammates or key in self.teammates

    def files(self) -> list[str]:
        """The other files of the skill, as relative paths (sorted, at most ``MAX_FILES``)."""

        found: list[str] = []

        def walk(folder: Traversable, prefix: str) -> None:
            for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
                if item.name.startswith(".") or len(found) >= MAX_FILES:
                    continue
                path = f"{prefix}{item.name}"
                if item.is_dir():
                    walk(item, f"{path}/")
                elif path != SKILL_FILE:
                    found.append(path)

        walk(self.folder, "")
        return found

    def read_file(self, relative: str) -> str:
        """One of :meth:`files` as text; ``ValueError`` names the files there are."""

        wanted = relative.strip().strip("/").replace("\\", "/")
        known = self.files()
        if wanted not in known:
            listed = ", ".join(known) or "none"
            raise ValueError(f"Skill '{self.name}' has no file {relative!r}; its files: {listed}.")
        target = self.folder
        for part in wanted.split("/"):
            target = target / part
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise ValueError(f"{wanted} of skill '{self.name}' is not readable text.") from exc
        if len(text) > MAX_FILE_CHARS:
            text = f"{text[:MAX_FILE_CHARS]}\n[... cut at {MAX_FILE_CHARS:,} characters]"
        return text


def load_skill(
    folder: Traversable, where: str, *, pack: str, source: str, trusted: bool, problems: list[str]
) -> Skill | None:
    """The skill in ``folder``, or ``None`` with a line per problem."""

    path = f"{where}/{SKILL_FILE}"
    if not (folder / SKILL_FILE).is_file():
        problems.append(f"{path}: missing; a skill is a folder with a SKILL.md.")
        return None
    text = read(folder / SKILL_FILE, path, problems)
    if text is None:
        return None
    data, body = split_front_matter(text, path, problems)
    unknown_fields(data, FIELDS, path, problems)
    name = str(data.get("name") or "")
    if name != folder.name or not NAME.fullmatch(name) or len(name) > MAX_NAME:
        problems.append(
            f"{path}: name must be the folder's name ({folder.name!r}): lowercase letters, digits "
            f"and hyphens, at most {MAX_NAME} characters."
        )
    description = " ".join(str(data.get("description") or "").split())
    if not description or len(description) > MAX_DESCRIPTION:
        problems.append(
            f"{path}: description must say when to use the skill in at most {MAX_DESCRIPTION} "
            f"characters (it has {len(description)})."
        )
    teammates = _teammates(data.get("teammates"), path, problems)
    if not body.strip():
        problems.append(f"{path}: the body is empty; write the skill's instructions there.")
    return Skill(
        name=name,
        description=description,
        body=body.strip() + "\n",
        teammates=teammates,
        pack=pack,
        source=source,
        trusted=trusted,
        folder=folder,
        where=path,
    )


def _teammates(value: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(v, str) and TEAM_KEY.fullmatch(v) for v in value
    ):
        problems.append(f"{where}: teammates must be a list of teammate keys.")
        return ()
    return tuple(value)


def skill_index(skills: list[Skill]) -> str:
    """The always-on line that lists ``skills`` (names and triggers only)."""

    if not skills:
        return ""
    items = "; ".join(
        f"{s.name} ({s.description.rstrip('.')}{'' if s.trusted else ', from the repository'})"
        for s in skills
    )
    return f"Skills (Load Skill returns one): {items}."
