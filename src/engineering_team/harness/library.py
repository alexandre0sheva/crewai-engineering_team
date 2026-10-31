"""The harness library: a domain's packs from every source, layered into one harness per run.

Sources, lowest first: the bundled library (``harness/data/``), the user's
(``~/.config/engineering-team/harness/``) and the repository's (``.engineering-team/harness/``).
Each holds pack folders (``base``, ``python``, ``bugfix``, ``bugfix+python``, ...). For each source
in turn, a domain's packs apply least specific first; a file replaces the one below it (a
teammate's components one by one, a stage, a shared text, a skill by name). The repository is
untrusted: it may add skills and guidance text (a teammate's ``tool_guidance.md`` and
``guardrails.md``, appended and labelled), never replace a skill, a stage, shared text or a
teammate's role, tier or tools.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from engineering_team.harness.domains import Domain
from engineering_team.harness.files import HarnessError, fold, read, split_front_matter
from engineering_team.harness.loader import (
    COMPONENTS,
    EVIDENCE_BOUNDARY,
    StagePrompt,
    _shared,
    _stage,
    _teammate,
)
from engineering_team.harness.skills import Skill, load_skill, skill_index
from engineering_team.settings import TEAM_KEY

REPOSITORY_HARNESS = Path(".engineering-team") / "harness"
PARTS = ("teammates", "stages", "shared", "skills")


@dataclass(frozen=True)
class Source:
    name: str  # bundled, user, repository
    root: Traversable
    label: str
    trusted: bool


@dataclass(frozen=True)
class PackRecord:
    name: str
    source: str
    sha256: str  # of the pack's files (path and content), first 12 hex digits


@dataclass
class ResolvedHarness:
    """One run's harness: its domain, the packs it used, and what they compose to."""

    domain: Domain
    packs: list[PackRecord]
    teammates: dict[str, dict[str, Any]]
    stages: dict[str, StagePrompt]
    shared: dict[str, str]
    skills: dict[str, Skill]
    loaded: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def skills_for(self, key: str) -> list[Skill]:
        return [s for s in self.skills.values() if s.visible_to(key)]

    def skill_index(self, key: str) -> str:
        return skill_index(self.skills_for(key))

    def lead_guidance(self) -> tuple[str, ...]:
        """``shared/lead_guidance.md``: one item per ``- `` bullet."""

        items: list[str] = []
        for number, line in enumerate(self.shared.get("lead_guidance", "").splitlines(), 1):
            if line.startswith("- "):
                items.append(line[2:].strip())
            elif line.strip() and items:
                items[-1] = f"{items[-1]} {line.strip()}"
            elif line.strip():
                raise HarnessError(
                    [f"shared/lead_guidance.md, line {number}: start each item with '- '."]
                )
        return tuple(items)

    def note_loaded(self, name: str) -> None:
        with self._lock:
            if name not in self.loaded:
                self.loaded.append(name)

    def record(self) -> dict[str, Any]:
        """What the manifest and the report keep (``RunManifest.harness``)."""

        with self._lock:
            loaded = list(self.loaded)
        return {
            "domain": self.domain.key,
            "packs": [{"name": p.name, "source": p.source, "sha256": p.sha256} for p in self.packs],
            "skills_listed": sorted(self.skills),
            "skills_loaded": loaded,
        }


def bundled() -> Source:
    return Source(
        "bundled", resources.files("engineering_team") / "harness" / "data", "harness/data", True
    )


def sources(repo_root: Path | None = None) -> list[Source]:
    """The bundled library, then the user's and the repository's, where they exist."""

    from engineering_team.settings.loading import user_config_directory

    found = [bundled()]
    user = user_config_directory()
    if user is not None and (user / "harness").is_dir():
        found.append(Source("user", user / "harness", str(user / "harness"), True))
    if repo_root is not None and (repo_root / REPOSITORY_HARNESS).is_dir():
        root = repo_root / REPOSITORY_HARNESS
        found.append(Source("repository", root, str(REPOSITORY_HARNESS), False))
    return found


def resolve(domain: Domain, layers: list[Source] | None = None) -> ResolvedHarness:
    """The harness of ``domain`` over ``layers`` (default: the bundled library only). Raises
    :class:`HarnessError` with one line per problem in any file it read."""

    problems: list[str] = []
    state = _State()
    for source in layers or [bundled()]:
        for pack in domain.packs():
            folder = source.root / pack
            if folder.is_dir():
                state.apply(folder, pack, source, problems)
    if EVIDENCE_BOUNDARY not in state.shared:
        problems.append(f"shared/{EVIDENCE_BOUNDARY}.md: missing from every pack; stages need it.")
    stages: dict[str, StagePrompt] = {}
    for key, (path, where) in sorted(state.stage_files.items()):
        prompt = _stage(path, key, where, state.shared, problems)
        if prompt is not None:
            stages[key] = prompt
    if problems:
        raise HarnessError(problems)
    return ResolvedHarness(
        domain=domain,
        packs=state.packs,
        teammates=state.teammates,
        stages=stages,
        shared={name: text for name, text in state.shared.items()},
        skills=dict(sorted(state.skills.items())),
    )


@dataclass
class _State:
    packs: list[PackRecord] = field(default_factory=list)
    teammates: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage_files: dict[str, tuple[Traversable, str]] = field(default_factory=dict)
    shared: dict[str, str] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)

    def apply(self, folder: Traversable, pack: str, source: Source, problems: list[str]) -> None:
        label = f"{source.label}/{pack}"
        self.packs.append(PackRecord(pack, source.name, _digest(folder)))
        for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
            if item.name.startswith(".") or (item.is_dir() and item.name in PARTS):
                continue
            problems.append(f"{label}/{item.name}: not part of a pack; use {', '.join(PARTS)}/.")
        if not source.trusted:
            self._repository(folder, label, pack, source, problems)
            return
        self._teammates(folder / "teammates", label, problems)
        if (folder / "stages").is_dir():
            for item in (folder / "stages").iterdir():
                if item.name.endswith(".md"):
                    key = item.name.removesuffix(".md")
                    self.stage_files[key] = (item, f"{label}/stages/{item.name}")
        self.shared.update(_shared(folder, label, problems))
        self._skills(folder / "skills", label, pack, source, problems)

    def _teammates(self, folder: Traversable, label: str, problems: list[str]) -> None:
        if not folder.is_dir():
            return
        for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
            where = f"{label}/teammates/{item.name}"
            if not TEAM_KEY.fullmatch(item.name):
                problems.append(f"{where}: a teammate folder is named like a key (data_engineer).")
                continue
            known = item.name in self.teammates
            fields = _teammate(item, where, not known, problems)
            self.teammates.setdefault(item.name, {}).update(fields)

    def _skills(
        self, folder: Traversable, label: str, pack: str, source: Source, problems: list[str]
    ) -> None:
        if not folder.is_dir():
            return
        for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
            if not item.is_dir():
                continue
            skill = load_skill(
                item,
                f"{label}/skills/{item.name}",
                pack=pack,
                source=source.name,
                trusted=source.trusted,
                problems=problems,
            )
            if skill is None:
                continue
            if not source.trusted and skill.name in self.skills:
                owner = self.skills[skill.name].source
                problems.append(
                    f"{skill.where}: a repository skill cannot replace the {owner} skill "
                    f"'{skill.name}'; give it a name of its own."
                )
                continue
            self.skills[skill.name] = skill

    def _repository(
        self, folder: Traversable, label: str, pack: str, source: Source, problems: list[str]
    ) -> None:
        for part in ("stages", "shared"):
            if (folder / part).is_dir():
                problems.append(
                    f"{label}/{part}/: a repository harness may only add skills and guidance "
                    "(teammates/<key>/tool_guidance.md, guardrails.md); put it in "
                    "~/.config/engineering-team/harness/ instead."
                )
        self._skills(folder / "skills", label, pack, source, problems)
        teammates = folder / "teammates"
        if not teammates.is_dir():
            return
        for member in sorted(teammates.iterdir(), key=lambda entry: entry.name):
            where = f"{label}/teammates/{member.name}"
            if member.name not in self.teammates:
                problems.append(f"{where}: no teammate '{member.name}' to add guidance to.")
                continue
            for item in sorted(member.iterdir(), key=lambda entry: entry.name):
                if not item.name.endswith(".md") or item.name[:-3] not in COMPONENTS:
                    problems.append(
                        f"{where}/{item.name}: a repository harness may only add tool_guidance.md "
                        "and guardrails.md (never role, tier or tools)."
                    )
                    continue
                text = read(item, f"{where}/{item.name}", problems)
                if text is None:
                    continue
                data, body = split_front_matter(text, f"{where}/{item.name}", problems)
                if data:
                    problems.append(f"{where}/{item.name}: no front-matter in guidance files.")
                fields = self.teammates[member.name]
                fields["repository"] = " ".join(
                    part for part in (fields.get("repository"), fold(body)) if part
                )


def _digest(folder: Traversable) -> str:
    """A content hash of every file under ``folder`` (relative path and bytes, sorted)."""

    digest = hashlib.sha256()

    def walk(item: Traversable, prefix: str) -> None:
        for entry in sorted(item.iterdir(), key=lambda e: e.name):
            path = f"{prefix}{entry.name}"
            if entry.is_dir():
                walk(entry, f"{path}/")
            else:
                digest.update(path.encode("utf-8") + b"\0" + entry.read_bytes() + b"\0")

    walk(folder, "")
    return digest.hexdigest()[:12]


def resolve_for_run(
    domain: Domain | None, previous: Path | None = None, repo_root: Path | None = None
) -> ResolvedHarness:
    """A run's harness over every source (``repo_root``: default the current directory). With
    no ``domain``, the one the run at ``previous`` recorded (a resumed run keeps its harness),
    else the ``base`` domain."""

    if domain is None and previous is not None:
        domain = recorded_domain(previous)
    return resolve(domain or Domain(), sources(repo_root or Path.cwd()))


def recorded_domain(run_dir: Path) -> Domain | None:
    """The domain in ``run_dir``'s manifest, if it recorded one (0.2.0 runs did not)."""

    try:
        data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = (data.get("harness") or {}).get("domain") if isinstance(data, dict) else None
    return Domain.parse(key) if isinstance(key, str) and key else None


def describe(harness: ResolvedHarness) -> str:
    """One line: ``bugfix/python · packs base, python, bugfix · skills a, b`` (sources shown
    when not bundled)."""

    packs = ", ".join(
        p.name if p.source == "bundled" else f"{p.name} ({p.source})" for p in harness.packs
    )
    skills = ", ".join(harness.skills) or "none"
    return f"{harness.domain.key} · packs {packs} · skills {skills}"
