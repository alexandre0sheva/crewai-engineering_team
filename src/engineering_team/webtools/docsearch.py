"""Search Docs: lightweight BM25 retrieval over local documents. No embeddings, no network.

The corpus is the project's own markdown/text documents (honouring ``.gitignore`` and the usual
heavy directories), the directories listed in ``knowledge.context_dirs`` (explicit user
configuration; symlinks are not followed), and the pages Fetch URL cached under
``run_dir/web-cache/``. Documents are split into passages by heading and paragraph, scored with
BM25 (identifiers are split into words, headings and file names count extra), and returned as
short excerpts. Whatever comes back is untrusted data: the tool labels it as such.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.workspace import IGNORED_LIST_DIRECTORIES, ProjectWorkspace

DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
SOURCES = ("repo", "context", "web-cache")
MAX_DOC_BYTES = 300_000
MAX_DOCUMENTS = 2000
CHUNK_CHARS = 1000
EXCERPT_CHARS = 400
K1, B = 1.5, 0.75
_STOP_TEXT = (
    "a an and are as at be but by for if in into is it of on or such that the their then there "
    "these they this to was will with"
)
STOP_WORDS = frozenset(_STOP_TEXT.split())
_CAMEL = re.compile(r"([a-z0-9])([A-Z])|([A-Z]+)([A-Z][a-z])")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def tokenize(text: str) -> list[str]:
    """Lower-case words; ``parseConfig`` and ``parse_config`` also yield their parts."""

    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z0-9_]+", text):
        spaced = _CAMEL.sub(
            lambda m: f"{m.group(1) or m.group(3)} {m.group(2) or m.group(4)}", word
        )
        parts = [p for p in re.split(r"[_\s]+", spaced.lower()) if p]
        if "_" in word and len(parts) > 1:
            tokens.append(word.lower())
        tokens.extend(parts)
    return [t for t in tokens if t not in STOP_WORDS and (len(t) > 1 or t.isdigit())]


@dataclass(frozen=True)
class Document:
    source: str  # repo, context, or web-cache
    path: str  # shown to the agent
    text: str


@dataclass(frozen=True)
class Chunk:
    source: str
    path: str
    line: int  # 1-based first line of the passage
    heading: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


@dataclass
class DocSources:
    workspace: ProjectWorkspace
    cache_dir: Path
    context_dirs: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[Hit]
    documents: int
    passages: int
    notes: list[str]


# -- corpus --------------------------------------------------------------------------------------


def _walk(directory: Path) -> list[Path]:
    """Documents below ``directory``: sorted, no symlinks, no hidden or heavy directories."""

    found: list[Path] = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in IGNORED_LIST_DIRECTORIES
            and not (Path(current) / d).is_symlink()
        )
        found.extend(
            Path(current) / name
            for name in sorted(files)
            if Path(name).suffix.lower() in DOC_SUFFIXES and not (Path(current) / name).is_symlink()
        )
    return found


def collect_documents(src: DocSources) -> tuple[list[Document], list[str]]:
    docs: list[Document] = []
    notes: list[str] = []

    def add(source: str, display: str, path: Path) -> None:
        if len(docs) < MAX_DOCUMENTS and (text := read_text_or_none(path, limit=MAX_DOC_BYTES)):
            docs.append(Document(source, display, text))

    ws = src.workspace
    for path in iter_files(ws, rules=IgnoreRules.for_workspace(ws)):
        if path.suffix.lower() in DOC_SUFFIXES:
            add("repo", ws.relative_name(path), path)
    for raw in src.context_dirs:
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            directory = Path.cwd() / directory
        if not directory.is_dir():
            notes.append(f"context dir not found: {directory}")
            continue
        for path in _walk(directory):
            add("context", f"{directory.name}/{path.relative_to(directory).as_posix()}", path)
    if src.cache_dir.is_dir():
        for path in sorted(src.cache_dir.glob("*.md")):
            if not path.is_symlink():
                add("web-cache", f"{src.cache_dir.name}/{path.name}", path)
    return docs, notes


def chunk_document(doc: Document) -> list[Chunk]:
    """Passages of about 1,000 characters, cut at headings and paragraph breaks."""

    chunks: list[Chunk] = []
    heading = ""
    buffer: list[str] = []
    start = 1

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(Chunk(doc.source, doc.path, start, heading, text))
        buffer.clear()

    for number, line in enumerate(doc.text.splitlines(), start=1):
        match = _HEADING.match(line)
        if match:
            flush()
            heading, start = match.group(2), number
            buffer.append(line)
            continue
        if not buffer:
            start = number
        buffer.append(line)
        if not line.strip() and sum(len(b) + 1 for b in buffer) >= CHUNK_CHARS:
            flush()
    flush()
    return chunks


# -- ranking -------------------------------------------------------------------------------------


class Bm25Index:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._tf: list[Counter[str]] = []
        for chunk in chunks:
            # The heading and the file name count extra: they say what the passage is about.
            words = tokenize(chunk.text) + tokenize(chunk.heading) * 2 + tokenize(chunk.path)
            self._tf.append(Counter(words))
        self._lengths = [sum(tf.values()) for tf in self._tf]
        self._average = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0
        document_frequency: Counter[str] = Counter()
        for tf in self._tf:
            document_frequency.update(tf.keys())
        count = len(chunks)
        self._idf = {
            word: math.log(1 + (count - n + 0.5) / (n + 0.5))
            for word, n in document_frequency.items()
        }

    def search(self, query: str, limit: int) -> list[Hit]:
        words = list(dict.fromkeys(tokenize(query)))
        scored: list[Hit] = []
        for index, tf in enumerate(self._tf):
            score = 0.0
            norm = 1 - B + B * self._lengths[index] / self._average
            for word in words:
                frequency = tf.get(word, 0)
                if frequency:
                    score += self._idf[word] * frequency * (K1 + 1) / (frequency + K1 * norm)
            if score > 0:
                scored.append(Hit(self.chunks[index], score))
        scored.sort(key=lambda hit: (-hit.score, hit.chunk.path, hit.chunk.line))
        return scored[:limit]


def search_docs(src: DocSources, query: str, limit: int = 5, *, source: str = "") -> SearchResult:
    """The best ``limit`` passages for ``query`` (optionally only from one ``source``)."""

    if source and source not in SOURCES:
        raise ValueError(f"source must be one of: {', '.join(SOURCES)} (or empty for all).")
    if not tokenize(query):
        raise ValueError("The query has no searchable words; use specific terms.")
    docs, notes = collect_documents(src)
    chunks = [
        chunk for doc in docs if not source or doc.source == source for chunk in chunk_document(doc)
    ]
    hits = Bm25Index(chunks).search(query, max(1, min(int(limit), 20)))
    return SearchResult(hits, len(docs), len(chunks), notes)


# -- output --------------------------------------------------------------------------------------


def _excerpt(chunk: Chunk, query: str) -> str:
    text = re.sub(r"\s+", " ", chunk.text).strip()
    if len(text) <= EXCERPT_CHARS:
        return text
    words = set(tokenize(query))
    position = 0
    for match in re.finditer(r"[A-Za-z0-9_]+", text):
        if words & set(tokenize(match.group(0))):
            position = match.start()
            break
    begin = max(0, min(position - 80, len(text) - EXCERPT_CHARS))
    cut = text[begin : begin + EXCERPT_CHARS].strip()
    return ("..." if begin else "") + cut + ("..." if begin + EXCERPT_CHARS < len(text) else "")


def format_hits(result: SearchResult, query: str) -> str:
    notes = [f"note: {note}" for note in result.notes]
    if not result.hits:
        return "\n".join(
            [f"No matching documents for '{query}' ({result.documents} documents scanned).", *notes]
        )
    lines = [
        f"{len(result.hits)} passage(s) for '{query}' "
        f"({result.documents} documents, {result.passages} passages scanned)"
    ]
    for number, hit in enumerate(result.hits, start=1):
        chunk = hit.chunk
        where = f"{chunk.path}:{chunk.line}" + (f" {chunk.heading}" if chunk.heading else "")
        lines.append(f"{number}. [{chunk.source}] {where}")
        lines.append(f"   {_excerpt(chunk, query)}")
    return "\n".join([*lines, *notes])
