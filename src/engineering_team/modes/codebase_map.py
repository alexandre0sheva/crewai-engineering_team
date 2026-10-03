"""The codebase map: what the analysts learned about an existing project, kept as one file.

``plan_chunks`` splits the source into at most *N* pieces (top-level packages or directories, by
size) for the analysts to read side by side. Each returns a :class:`ChunkAnalysis`; a synthesis
step returns a :class:`CodebaseMap`; the controller renders it to
``.engineering-team/codebase-map.md`` (``render_map``) so its structure and size are the
controller's, not the agent's. The file starts with the hash of the tree it describes, so it is
reused for as long as the tree is unchanged (``cached_map``), and ``map_context`` hands later
modes a size-capped copy for agent context.

The map is a guide written by agents from reading the code; it is never evidence for anything.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from pydantic import Field

from engineering_team.atomic_io import atomic_write_text
from engineering_team.contracts import Contract
from engineering_team.modes.repo_analyzer import LANGUAGES
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.tools.ignore import IgnoreRules, iter_files
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace

MAP_FILE = Path(CONTROLLER_DIRECTORY) / "codebase-map.md"
HEADER = re.compile(r"\A<!-- engineering-team:codebase-map tree=([0-9a-f]+) -->\n")
SCRATCH = f"{CONTROLLER_DIRECTORY}/tmp/analysis"
MAX_ITEMS = 30
MAX_ITEM_CHARS = 300
MAX_MODULES = 60
MAX_BRIEF_FILES = 60
ROOT_FILES = "(root files)"
DOMINANT = 0.5  # a chunk holding more than this share of the source is split further


# -- contracts the analysts return ---------------------------------------------------------


class ModuleNote(Contract):
    name: str
    path: str = ""
    purpose: str = ""
    depends_on: list[str] = Field(default_factory=list)


class ChunkAnalysis(Contract):
    """What one analyst learned about its chunk."""

    summary: str = ""
    modules: list[ModuleNote] = Field(default_factory=list)
    key_flows: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)  # how this part is tested, and what is not


class CodebaseMap(Contract):
    """The synthesis: the whole project, from the chunk analyses and the deterministic profile."""

    overview: str = ""
    architecture: str = ""
    modules: list[ModuleNote] = Field(default_factory=list)
    key_flows: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)
    hotspots: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    how_to_run: list[str] = Field(default_factory=list)
    how_to_test: list[str] = Field(default_factory=list)


# -- splitting the source -------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """A piece of the project one analyst reads: ``paths`` are directories (with a trailing
    ``/``) or single files, relative to the project root."""

    name: str
    paths: tuple[str, ...]
    files: tuple[str, ...]  # the source files in it, sorted
    bytes: int
    slug: str = ""  # unique among the chunks of one project; names the analyst's scratch file

    @property
    def report_path(self) -> str:
        """Scratch space the analyst's write tools are scoped to (no source file is reachable)."""

        return f"{SCRATCH}/{self.slug}.md"

    def brief(self) -> str:
        shown = self.files[:MAX_BRIEF_FILES]
        more = len(self.files) - len(shown)
        listing = "\n".join(f"- {name}" for name in shown)
        if more > 0:
            listing += f"\n- ... and {more} more"
        return (
            f"Chunk '{self.name}': {len(self.files)} source file(s), {self.bytes:,} bytes.\n"
            f"Paths: {', '.join(self.paths)}\n\nFiles:\n{listing}"
        )


def plan_chunks(workspace: ProjectWorkspace, max_chunks: int) -> list[Chunk]:
    """Split the project's source files into at most ``max_chunks`` chunks.

    Top-level entries are the units; a unit that holds most of the source (a ``src/`` with
    everything in it) is split one level down, and when there are still too many the two
    smallest are merged. Deterministic: sizes and names decide, nothing else.
    """

    sizes: dict[str, int] = {}
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        if path.suffix.lower() not in LANGUAGES:
            continue
        try:
            sizes[workspace.relative_name(path)] = path.stat().st_size
        except OSError:
            continue
    if not sizes:
        return []
    total = sum(sizes.values())
    units: dict[str, list[str]] = _group(list(sizes), 1)
    for _ in range(3):  # at most three levels deep
        weight = {key: sum(sizes[f] for f in files) for key, files in units.items()}
        biggest = max(weight, key=lambda k: (weight[k], k))
        splittable = len(units) < max_chunks and weight[biggest] > DOMINANT * total
        children = _group(units[biggest], _depth(biggest) + 1) if splittable else {}
        if len(children) < 2:
            break
        del units[biggest]
        units.update(children)
    chunks = [_chunk(key, files, sizes) for key, files in units.items()]
    while len(chunks) > max(1, max_chunks):
        chunks.sort(key=lambda c: (c.bytes, c.name))
        small, other, *rest = chunks
        chunks = [_merge(small, other), *rest]
    return _with_slugs(sorted(chunks, key=lambda c: c.name))


def _with_slugs(chunks: list[Chunk]) -> list[Chunk]:
    seen: set[str] = set()
    named = []
    for chunk in chunks:
        base = re.sub(r"[^a-z0-9]+", "-", chunk.name.lower()).strip("-")[:50] or "chunk"
        slug, number = base, 1
        while slug in seen:
            number += 1
            slug = f"{base}-{number}"
        seen.add(slug)
        named.append(replace(chunk, slug=slug))
    return named


def _depth(key: str) -> int:
    return 0 if key == ROOT_FILES else len(PurePosixPath(key.rstrip("/")).parts)


def _group(files: list[str], depth: int) -> dict[str, list[str]]:
    """Group files by their first ``depth`` directories; a file closer to the root than that
    goes with the directory it is in (loose root files share one group)."""

    groups: dict[str, list[str]] = defaultdict(list)
    for name in files:
        parts = PurePosixPath(name).parts
        if len(parts) == 1:
            groups[ROOT_FILES].append(name)
        elif len(parts) - 1 <= depth:
            groups["/".join(parts[:-1]) + "/"].append(name)
        else:
            groups["/".join(parts[:depth]) + "/"].append(name)
    return dict(groups)


def _chunk(key: str, files: list[str], sizes: dict[str, int]) -> Chunk:
    ordered = tuple(sorted(files))
    paths = ordered if key == ROOT_FILES else (key,)
    name = key.rstrip("/") if key != ROOT_FILES else ROOT_FILES
    return Chunk(name, paths, ordered, sum(sizes[f] for f in ordered))


def _merge(a: Chunk, b: Chunk) -> Chunk:
    first, second = sorted((a, b), key=lambda c: c.name)
    return Chunk(
        f"{first.name} + {second.name}",
        (*first.paths, *second.paths),
        tuple(sorted({*first.files, *second.files})),
        a.bytes + b.bytes,
    )


# -- the file -------------------------------------------------------------------------------


def map_path(root: Path) -> Path:
    return root / MAP_FILE


def cached_map(root: Path, tree: str) -> Path | None:
    """The map file if it describes exactly the tree ``tree``, else ``None``."""

    try:
        head = map_path(root).read_text(encoding="utf-8")[:200]
    except OSError:
        return None
    found = HEADER.match(head)
    return map_path(root) if found and found.group(1) == tree else None


def write_map(root: Path, tree: str, text: str) -> Path:
    path = map_path(root)
    atomic_write_text(path, f"<!-- engineering-team:codebase-map tree={tree} -->\n{text}")
    return path


def map_context(root: Path, cap: int) -> str:
    """The map as agent context, at most ``cap`` characters (cut at a line); empty if none."""

    try:
        text = map_path(root).read_text(encoding="utf-8")
    except OSError:
        return ""
    text = HEADER.sub("", text, count=1).strip()
    if len(text) <= cap:
        return text
    cut = text[:cap].rsplit("\n", 1)[0]
    return f"{cut}\n\n[The codebase map was cut to {cap} characters.]"


# -- rendering ------------------------------------------------------------------------------


def _one_line(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _bullets(items: list[str]) -> list[str]:
    kept = [_one_line(item) for item in items if str(item).strip()][:MAX_ITEMS]
    return [f"- {item}" for item in kept] or ["- (none reported)"]


def render_map(
    synthesis: CodebaseMap, profile: RepoProfile, chunks: list[Chunk], failed: list[str]
) -> str:
    """The map as Markdown: the analysts' words in the controller's structure, with the facts
    the controller itself found (languages, commands) stated as such."""

    lines = [
        f"# Codebase map: {profile.name}",
        "",
        "Written by the codebase analysts from reading the code; the facts in *Detected "
        "facts* were found by the controller. A guide, not evidence: check before relying "
        "on it.",
        "",
        "## Overview",
        "",
        _one_line(synthesis.overview, 1500) or "(none reported)",
        "",
        "## Detected facts",
        "",
        f"- Size: {profile.files:,} files, {profile.lines:,} lines of code",
        "- Languages: "
        + (", ".join(f"{s.language} ({s.lines:,} lines)" for s in profile.languages[:6]) or "none"),
    ]
    lines += [f"- Project: {s.directory}: {s.language} ({s.manager})" for s in profile.stacks]
    for kind in ("setup", "test", "lint", "typecheck", "build", "run"):
        for command in profile.commands_of(kind)[:3]:
            where = "" if command.directory == "." else f" (in {command.directory})"
            lines.append(f"- {kind}: `{command.command}`{where}")
    lines += [f"- Entry point: `{e.path}` ({e.evidence})" for e in profile.entrypoints[:8]]
    if profile.test_dirs:
        lines.append(f"- Tests: {', '.join(profile.test_dirs[:6])} ({profile.test_files} files)")
    lines += ["", "## Architecture", "", _one_line(synthesis.architecture, 3000) or "(none)", ""]
    lines += ["## Modules", ""]
    for module in synthesis.modules[:MAX_MODULES]:
        where = f" (`{module.path}`)" if module.path else ""
        uses = f" Uses: {', '.join(module.depends_on[:6])}." if module.depends_on else ""
        lines.append(
            f"- **{_one_line(module.name, 80)}**{where}: {_one_line(module.purpose)}{uses}"
        )
    if not synthesis.modules:
        lines.append("- (none reported)")
    for title, items in (
        ("Key flows", synthesis.key_flows),
        ("Conventions", synthesis.conventions),
        ("Hotspots", synthesis.hotspots),
        ("Risks", synthesis.risks),
        ("How to run", synthesis.how_to_run),
        ("How to test", synthesis.how_to_test),
    ):
        lines += ["", f"## {title}", "", *_bullets(items)]
    lines += ["", "## Coverage", ""]
    lines += [
        f"- {c.name}: {len(c.files)} file(s)" + (" (analysis failed)" if c.name in failed else "")
        for c in chunks
    ]
    return "\n".join(lines) + "\n"
