"""Candidate harness trees: what a proposer may change, how a candidate is checked, and its diff.

A candidate is a set of whole files in one pack, the domain's most specific (``bugfix+python``
for ``bugfix/python``), laid over the bundled library as the user's harness would be. The INV
proposer changes procedure: a teammate's ``tool_guidance.md`` or ``guardrails.md``, and
``shared/completion.md``; the DOM proposer changes knowledge: ``skills/<name>/SKILL.md``. A
candidate is valid when the harness still loads with it (schema), every file is under its cap and
every teammate's always-on text under ``ALWAYS_ON_CAP`` (caps), every skill has a trigger of its
own and only names teammates that exist (triggers), and no file quotes a secret or a protected
file (a benchmark's hidden checks and references: the leak guard).
"""

from __future__ import annotations

import difflib
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from engineering_team.harness import HarnessError
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import ResolvedHarness, Source, bundled, resolve
from engineering_team.harness.loader import ALWAYS_ON_CAP
from engineering_team.learning.guard import Protected, guard

if TYPE_CHECKING:
    from engineering_team.settings import Settings

Kind = Literal["inv", "dom", "composed"]
PROCEDURE = re.compile(
    r"teammates/(?P<key>[a-z][a-z0-9_]*)/(?:tool_guidance|guardrails)\.md|shared/completion\.md"
)
KNOWLEDGE = re.compile(r"skills/(?P<skill>[a-z0-9]+(?:-[a-z0-9]+)*)/SKILL\.md")
MAX_EDITS = 4
MAX_COMPONENT_CHARS = 1_500
MAX_SKILL_CHARS = 6_000


@dataclass(frozen=True)
class Candidate:
    """Whole files by pack-relative path (an empty candidate is the base harness)."""

    label: str
    kind: Kind
    edits: Mapping[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.edits

    def paths(self) -> list[str]:
        return sorted(self.edits)


def pack_of(domain: Domain) -> str:
    """The pack a candidate is written to: the domain's most specific."""

    return domain.packs()[-1]


def allowed(kind: Kind, path: str) -> bool:
    patterns = {"inv": (PROCEDURE,), "dom": (KNOWLEDGE,), "composed": (PROCEDURE, KNOWLEDGE)}
    return any(p.fullmatch(path) for p in patterns[kind])


def base_source(domain: Domain, path: str) -> tuple[str, str] | None:
    """``(pack, text)`` of the bundled file at ``path`` in the most specific of the domain's packs
    that has it."""

    root = bundled().root
    for pack in reversed(domain.packs()):
        target = root / pack
        for part in path.split("/"):
            target = target / part
        if target.is_file():
            return pack, target.read_text(encoding="utf-8")
    return None


def base_file(domain: Domain, path: str) -> str | None:
    found = base_source(domain, path)
    return None if found is None else found[1]


def write_overlay(candidate: Candidate, pack: str, root: Path) -> Path:
    """Write ``candidate`` as ``<root>/<pack>/<path>``; returns ``root``."""

    root.mkdir(parents=True, exist_ok=True)
    for path, text in sorted(candidate.edits.items()):
        target = root / pack / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def resolve_with(domain: Domain, candidate: Candidate, root: Path) -> ResolvedHarness:
    """The domain's harness with ``candidate`` over the bundled library (raises HarnessError)."""

    write_overlay(candidate, pack_of(domain), root)
    return resolve(
        domain, [bundled(), Source("tuning", root, f"candidate {candidate.label}", True)]
    )


def merge(label: str, candidates: Iterable[Candidate]) -> Candidate:
    """The union of ``candidates``' files; the first to set a path keeps it."""

    edits: dict[str, str] = {}
    for candidate in candidates:
        for path, text in candidate.edits.items():
            edits.setdefault(path, text)
    return Candidate(label, "composed", dict(sorted(edits.items())))


def diff(candidate: Candidate, domain: Domain) -> str:
    """A unified diff of the candidate against the bundled files it overrides (``a/`` names the
    pack the bundled file is in, ``b/`` the candidate's pack)."""

    pack = pack_of(domain)
    chunks: list[str] = []
    for path in candidate.paths():
        found = base_source(domain, path)
        chunks.extend(
            difflib.unified_diff(
                found[1].splitlines(keepends=True) if found else [],
                _ending(candidate.edits[path]).splitlines(keepends=True),
                fromfile=f"a/{found[0]}/{path}" if found else "/dev/null",
                tofile=f"b/{pack}/{path}",
            )
        )
    return "".join(chunks)


def _ending(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


@dataclass
class Checker:
    """What a candidate is validated against: the domain, its base harness and the guards."""

    domain: Domain
    base: ResolvedHarness
    settings: Settings
    protected: Protected = field(default_factory=Protected)
    secrets: tuple[str, ...] = ()

    def problems(self, candidate: Candidate, kind: Kind | None = None) -> list[str]:
        """One line per problem (empty: valid). ``kind`` limits the paths (default: the
        candidate's own kind)."""

        kind = kind or candidate.kind
        problems: list[str] = []
        if len(candidate.edits) > MAX_EDITS and kind != "composed":
            problems.append(f"{len(candidate.edits)} files changed; change at most {MAX_EDITS}.")
        for path, text in sorted(candidate.edits.items()):
            problems.extend(self._file(kind, path, text))
        if problems:
            return problems
        with tempfile.TemporaryDirectory(prefix="tune-") as scratch:
            try:
                harness = resolve_with(self.domain, candidate, Path(scratch))
            except HarnessError as exc:
                return list(exc.problems)
            problems.extend(self._skills(candidate, harness))
            problems.extend(self._always_on(harness))
        return problems

    def _file(self, kind: Kind, path: str, text: str) -> list[str]:
        if not allowed(kind, path):
            return [f"{path}: {_scope(kind)}"]
        problems: list[str] = []
        key = PROCEDURE.fullmatch(path)
        if key and key.group("key") and key.group("key") not in self.base.teammates:
            problems.append(f"{path}: there is no teammate {key.group('key')!r}.")
        cap = MAX_SKILL_CHARS if KNOWLEDGE.fullmatch(path) else MAX_COMPONENT_CHARS
        if not text.strip():
            problems.append(f"{path}: empty; leave a file out to keep the base's.")
        elif len(text) > cap:
            problems.append(f"{path}: {len(text):,} characters; the cap is {cap:,}.")
        reason = guard([text], self.protected, self.secrets)
        if reason is not None:
            problems.append(f"{path}: {reason}; write guidance that generalises instead.")
        return problems

    def _skills(self, candidate: Candidate, harness: ResolvedHarness) -> list[str]:
        problems: list[str] = []
        triggers: dict[str, list[str]] = {}
        for skill in harness.skills.values():
            triggers.setdefault(skill.description.lower(), []).append(skill.name)
        for path in candidate.paths():
            match = KNOWLEDGE.fullmatch(path)
            if not match or match.group("skill") not in harness.skills:
                continue
            skill = harness.skills[match.group("skill")]
            others = [n for n in triggers[skill.description.lower()] if n != skill.name]
            if others:
                problems.append(
                    f"{path}: its description repeats the trigger of skill '{others[0]}'; say "
                    "when this skill is the one to load."
                )
            unknown = [key for key in skill.teammates if key not in harness.teammates]
            if unknown:
                problems.append(f"{path}: teammates {', '.join(unknown)} do not exist.")
        return problems

    def _always_on(self, harness: ResolvedHarness) -> list[str]:
        from engineering_team.team import build_roster

        try:
            roster = build_roster(self.settings, harness=harness, plugin_groups=())
        except ValueError as exc:
            return [f"the team does not load with it: {exc}"]
        return [
            f"teammates/{m.key}: its always-on text would be {m.always_on_chars:,} characters "
            f"(cap {ALWAYS_ON_CAP:,}); shorten it, or list fewer teammates in a skill's "
            "front-matter."
            for m in roster.all()
            if m.always_on_chars > ALWAYS_ON_CAP
        ]


def _scope(kind: Kind) -> str:
    if kind == "inv":
        return (
            "not a procedure component; change teammates/<key>/tool_guidance.md, "
            "teammates/<key>/guardrails.md or shared/completion.md."
        )
    if kind == "dom":
        return "not a skill; write skills/<name>/SKILL.md (lowercase name with hyphens)."
    return "not a component tuning may change."
