"""The experience library: lessons per domain, one folder per lesson, each with ``EXPERIENCE.md``.

Two libraries exist: the user's (``$XDG_DATA_HOME/engineering-team/library/``, default
``~/.local/share/...``), shared by every repository, and optionally a repository's own
(``.engineering-team/library/``). Inside one, a domain ``bugfix/python`` is the folder
``bugfix/python/`` (``*`` is written ``any``); each lesson is ``<domain>/<id>/EXPERIENCE.md`` and
each domain folder has an ``INDEX.md`` rebuilt from disk on every change (never read back).
Lessons waiting for review (``learning.review = "manual"``) live under ``pending/<domain>/`` until
``engineering-team lessons approve``. The files are the data: a person may edit the cue and the
lesson; the loader checks every file and reports one line per problem.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engineering_team.atomic_io import atomic_write_text
from engineering_team.harness.domains import ANY
from engineering_team.harness.files import SECTION, split_front_matter
from engineering_team.learning.lessons import Lesson
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

REPOSITORY_LIBRARY = Path(CONTROLLER_DIRECTORY) / "library"
ENTRY_FILE = "EXPERIENCE.md"
INDEX_FILE = "INDEX.md"
PENDING = "pending"
ANY_FOLDER = "any"
SECTIONS = ("Cue", "Lesson")


def user_library() -> Path | None:
    """``$XDG_DATA_HOME/engineering-team/library`` (``~/.local/share`` without it); ``None``
    when there is no home directory."""

    from engineering_team.settings.loading import user_data_directory

    base = user_data_directory()
    return None if base is None else base / "library"


def domain_folder(domain: str) -> Path:
    """``bugfix/python`` -> ``bugfix/python``; ``*`` parts become ``any``."""

    intent, _, stack = domain.partition("/")
    return Path(*(ANY_FOLDER if part in ("", ANY) else part for part in (intent, stack)))


def folder_domain(folder: Path) -> str:
    return "/".join(ANY if part == ANY_FOLDER else part for part in folder.parts[-2:])


class LibraryError(ValueError):
    """Lesson files that cannot be used; one line per problem."""


def render_entry(lesson: Lesson) -> str:
    """``EXPERIENCE.md``: the metadata and evidence as front-matter, the cue and the lesson as
    sections a person can edit."""

    data = lesson.model_dump(mode="json", exclude={"cue", "lesson"})
    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return (
        f"---\n{front}---\n# Lesson {lesson.id} ({lesson.domain})\n\n"
        f"## Cue\n\n{lesson.cue}\n\n## Lesson\n\n{lesson.lesson}\n"
    )


def parse_entry(text: str, where: str, problems: list[str]) -> Lesson | None:
    """The lesson in an ``EXPERIENCE.md`` text (``None`` and problems when it is not one)."""

    found: list[str] = []
    data, body = split_front_matter(text, where, found)
    if not data and not found:
        found.append(f"{where}: no front-matter; an entry starts with --- and its fields.")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        heading = SECTION.match(line)
        if heading:
            current = heading.group(1).strip()
            if current not in SECTIONS:
                found.append(f"{where}: unknown section '## {current}'; use ## Cue and ## Lesson.")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    for name in SECTIONS:
        if not " ".join(sections.get(name, [])).strip():
            found.append(f"{where}: the '## {name}' section is missing or empty.")
    if found:
        problems.extend(found)
        return None
    fields: dict[str, Any] = dict(data)
    fields["cue"] = " ".join(" ".join(sections["Cue"]).split())
    fields["lesson"] = " ".join(" ".join(sections["Lesson"]).split())
    try:
        return Lesson.model_validate(fields)
    except ValidationError as exc:
        for error in exc.errors()[:5]:
            field = ".".join(str(p) for p in error["loc"]) or "entry"
            problems.append(f"{where}: {field}: {error['msg']}.")
        return None


def render_index(domain: str, lessons: list[Lesson], *, pending: bool = False) -> str:
    title = f"# {'Pending lessons' if pending else 'Lessons'}: {domain}\n\n"
    if not lessons:
        return title + "No lessons.\n"
    lines = [
        "Generated from the entries in this folder; edit those, not this file.\n",
        "| Id | Confidence | Component | Verdict | Cue |",
        "|---|---|---|---|---|",
    ]
    for item in lessons:
        cue = item.cue.replace("|", "\\|")
        lines.append(
            f"| [{item.id}]({item.id}/{ENTRY_FILE}) | {item.confidence} | `{item.component}` | "
            f"{item.evidence.verdict} | {cue} |"
        )
    return title + "\n".join(lines) + "\n"


class LessonLibrary:
    """One library folder: ``user`` or ``repository``."""

    def __init__(self, root: Path, *, name: str = "user", max_lessons: int = 100) -> None:
        self.root = root
        self.name = name
        self.max_lessons = max_lessons

    def folder(self, domain: str, *, pending: bool = False) -> Path:
        return (self.root / PENDING if pending else self.root) / domain_folder(domain)

    # -- reading ---------------------------------------------------------------------------

    def _entries(self, pending: bool) -> list[Path]:
        base = self.root / PENDING if pending else self.root
        if not base.is_dir():
            return []
        found = sorted(base.glob(f"*/*/*/{ENTRY_FILE}"))
        return [p for p in found if pending or p.relative_to(base).parts[0] != PENDING]

    def load(
        self, domains: tuple[str, ...] | None = None, *, pending: bool = False
    ) -> tuple[list[Lesson], list[str]]:
        """The lessons (of ``domains`` only, if given) and one line per file that is not one."""

        lessons: list[Lesson] = []
        problems: list[str] = []
        wanted = {domain_folder(d) for d in domains} if domains is not None else None
        base = self.root / PENDING if pending else self.root
        for path in self._entries(pending):
            folder = path.parent.parent.relative_to(base)
            if wanted is not None and folder not in wanted:
                continue
            where = str(path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                problems.append(f"{where}: cannot be read ({exc}); delete the folder.")
                continue
            lesson = parse_entry(text, where, problems)
            if lesson is None:
                continue
            if lesson.id != path.parent.name or domain_folder(lesson.domain) != folder:
                problems.append(
                    f"{where}: id {lesson.id} / domain {lesson.domain} do not match the folder "
                    f"{folder / path.parent.name}; move the entry or fix its front-matter."
                )
                continue
            lessons.append(lesson)
        return lessons, problems

    def find(self, ident: str) -> tuple[Path, bool] | None:
        """The folder of the lesson with this id (or the only one it begins), and whether it
        is pending."""

        matches = [
            (p.parent, pending)
            for pending in (False, True)
            for p in self._entries(pending)
            if p.parent.name == ident or (ident and p.parent.name.startswith(ident))
        ]
        exact = [m for m in matches if m[0].name == ident]
        found = exact or matches
        return found[0] if len(found) == 1 else None

    # -- writing ---------------------------------------------------------------------------

    def add(self, lesson: Lesson, *, pending: bool = False) -> Path | None:
        """Write the lesson (``None`` when the same lesson is already there, pending or not)."""

        if self.find(lesson.id) is not None:
            return None
        folder = self.folder(lesson.domain, pending=pending) / lesson.id
        atomic_write_text(folder / ENTRY_FILE, render_entry(lesson))
        self._trim(lesson.domain, pending=pending)
        self.reindex(lesson.domain, pending=pending)
        return folder

    def _trim(self, domain: str, *, pending: bool) -> None:
        """At most ``max_lessons`` per domain: the oldest go first."""

        lessons, _ = self.load((domain,), pending=pending)
        if len(lessons) <= self.max_lessons:
            return
        by_age: list[tuple[datetime, str]] = sorted((item.created, item.id) for item in lessons)
        for _, ident in by_age[: len(lessons) - self.max_lessons]:
            shutil.rmtree(self.folder(domain, pending=pending) / ident, ignore_errors=True)

    def approve(self, ident: str) -> Lesson:
        """Move a pending lesson into its domain. Raises :class:`LibraryError`."""

        found = self.find(ident)
        if found is None or not found[1]:
            raise LibraryError(f"No pending lesson {ident!r}; `lessons list --pending` lists them.")
        folder, _ = found
        problems: list[str] = []
        entry = folder / ENTRY_FILE
        lesson = parse_entry(entry.read_text(encoding="utf-8"), str(entry), problems)
        if lesson is None:
            raise LibraryError("\n".join(problems))
        approved = lesson.model_copy(update={"review": "approved"})
        target = self.folder(lesson.domain) / lesson.id
        atomic_write_text(target / ENTRY_FILE, render_entry(approved))
        shutil.rmtree(folder, ignore_errors=True)
        self.reindex(lesson.domain)
        self.reindex(lesson.domain, pending=True)
        return approved

    def remove(self, ident: str) -> str | None:
        """Delete a lesson (pending or not); returns its id, ``None`` if there is none."""

        found = self.find(ident)
        if found is None:
            return None
        folder, pending = found
        domain = folder_domain(folder.parent)
        shutil.rmtree(folder)
        self.reindex(domain, pending=pending)
        return folder.name

    def reindex(self, domain: str, *, pending: bool = False) -> None:
        """Rebuild the domain's ``INDEX.md`` from the entries on disk."""

        folder = self.folder(domain, pending=pending)
        if not folder.is_dir():
            return
        lessons, _ = self.load((domain,), pending=pending)
        lessons.sort(key=lambda item: (item.created, item.id))
        if not lessons and not any(p.is_dir() for p in folder.iterdir()):
            (folder / INDEX_FILE).unlink(missing_ok=True)
            return
        atomic_write_text(folder / INDEX_FILE, render_index(domain, lessons, pending=pending))


def libraries(
    repo_root: Path | None, *, max_lessons: int = 100, repository: bool = True
) -> list[LessonLibrary]:
    """The user's library, then the repository's (when ``repository`` and it exists)."""

    found: list[LessonLibrary] = []
    user = user_library()
    if user is not None:
        found.append(LessonLibrary(user, name="user", max_lessons=max_lessons))
    if repository and repo_root is not None and (repo_root / REPOSITORY_LIBRARY).is_dir():
        found.append(
            LessonLibrary(
                repo_root / REPOSITORY_LIBRARY, name="repository", max_lessons=max_lessons
            )
        )
    return found
