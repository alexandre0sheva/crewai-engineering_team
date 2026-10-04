"""Optional CrewAI knowledge sources: documents embedded by a provider you choose.

Off unless ``knowledge.sources`` is set, and then ``knowledge.embedder`` is required, so the
provider (and its cost and credential) is never implicit. The documents are sent to that provider
to be embedded; with ``ollama`` they stay on the machine. This is separate from *Search Docs*
(local BM25, offline, always available): use it when meaning matters more than exact words.

The controller reads the files itself (text documents only, symlinks skipped, size and count
capped) and hands CrewAI their text, so CrewAI's own ``knowledge/`` directory rules never apply.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crewai.knowledge.source.base_knowledge_source import BaseKnowledgeSource
from crewai.knowledge.source.string_knowledge_source import StringKnowledgeSource

from engineering_team.settings import KnowledgeSettings, Settings
from engineering_team.tools.workspace import IGNORED_LIST_DIRECTORIES
from engineering_team.webtools.docsearch import DOC_SUFFIXES, MAX_DOC_BYTES

MAX_FILES = 50
MAX_TOTAL_BYTES = 1_000_000


class KnowledgeError(ValueError):
    """A knowledge source that cannot be used; the message says what to fix."""


@dataclass(frozen=True)
class KnowledgeDoc:
    label: str  # the path as configured, plus the file's relative name inside a directory
    text: str


def _expand(entry: str, base: Path) -> list[tuple[str, Path]]:
    path = Path(entry).expanduser()
    path = path if path.is_absolute() else base / path
    if path.is_symlink():
        raise KnowledgeError(f"knowledge source {entry!r} is a symlink; point at the real path.")
    if path.is_file():
        return [(entry, path)]
    if not path.is_dir():
        raise KnowledgeError(f"knowledge source {entry!r} ({path}) does not exist.")
    found: list[tuple[str, Path]] = []
    for current, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in IGNORED_LIST_DIRECTORIES
        )
        base_dir = Path(current)
        found.extend(
            (f"{entry}/{(base_dir / n).relative_to(path).as_posix()}", base_dir / n)
            for n in sorted(names)
            if not n.startswith(".")
            and (base_dir / n).suffix.lower() in DOC_SUFFIXES
            and not (base_dir / n).is_symlink()
        )
    return found


def collect_documents(settings: KnowledgeSettings, base: Path) -> list[KnowledgeDoc]:
    """The text of every document ``knowledge.sources`` names, in a stable order.

    Raises :class:`KnowledgeError` when a source is missing, a file is not a text document, or a
    cap (``MAX_FILES`` files, ``MAX_TOTAL_BYTES`` bytes) is exceeded: nothing is cut off silently.
    """

    documents: list[KnowledgeDoc] = []
    seen: set[Path] = set()
    total = 0
    for entry in settings.sources:
        for label, path in _expand(entry, base):
            if path.resolve() in seen:
                continue  # named twice (a file and its directory): embedded once
            seen.add(path.resolve())
            if path.suffix.lower() not in DOC_SUFFIXES:
                raise KnowledgeError(
                    f"knowledge source {label} is not a text document "
                    f"({', '.join(sorted(DOC_SUFFIXES))})."
                )
            size = path.stat().st_size
            if size > MAX_DOC_BYTES:
                raise KnowledgeError(
                    f"knowledge source {label} is {size:,} bytes; the limit is {MAX_DOC_BYTES:,}."
                )
            total += size
            documents.append(
                KnowledgeDoc(label, path.read_text(encoding="utf-8", errors="replace"))
            )
    if len(documents) > MAX_FILES or total > MAX_TOTAL_BYTES:
        raise KnowledgeError(
            f"knowledge.sources holds {len(documents)} documents ({total:,} bytes); the limits are "
            f"{MAX_FILES} documents and {MAX_TOTAL_BYTES:,} bytes. Narrow knowledge.sources."
        )
    return documents


def embedder_config(settings: KnowledgeSettings) -> dict[str, Any] | None:
    """CrewAI's ``embedder`` argument for ``knowledge.embedder``."""

    embedder = settings.embedder
    if embedder is None:
        return None
    config: dict[str, Any] = {}
    if embedder.model:
        config["model" if embedder.provider == "voyageai" else "model_name"] = embedder.model
    if embedder.url and embedder.provider == "ollama":
        config["url"] = embedder.url
    return {"provider": embedder.provider, "config": config}


def knowledge_for(
    settings: Settings, teammate: str, base: Path | None = None
) -> tuple[list[BaseKnowledgeSource], dict[str, Any]] | None:
    """``(knowledge_sources, embedder)`` for ``Agent(...)``, or ``None`` (not configured, or the
    teammate is not in ``knowledge.roles``). Building them embeds nothing; CrewAI does that when
    the agent first needs them."""

    knowledge = settings.knowledge
    if not knowledge.sources or (knowledge.roles and teammate not in knowledge.roles):
        return None
    embedder = embedder_config(knowledge)
    if embedder is None:
        return None
    sources: list[BaseKnowledgeSource] = [
        StringKnowledgeSource(content=doc.text, metadata={"source": doc.label})
        for doc in collect_documents(knowledge, base or Path.cwd())
    ]
    return (sources, embedder) if sources else None
