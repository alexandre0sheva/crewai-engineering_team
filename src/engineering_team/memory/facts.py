"""The data of repo memory: ``Fact`` and the checks every fact passes before it is stored.

A fact is one line of verified knowledge about a repository (the command that runs its tests, a
setup step that worked, a test that flips on its own), the evidence it rests on, and the files
whose content it depends on (``sources``, with their SHA-256). When a source changes, the fact
goes stale and is no longer shown to the team (``docs/HARNESS.md#repo-memory``). Facts are
untrusted data: they shape prompts and nothing else.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_team.contracts import utc_now
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY

FactKind = Literal["command", "setup", "convention", "layout", "flaky_test", "gotcha"]
FACT_KINDS: tuple[str, ...] = (
    "command",
    "setup",
    "convention",
    "layout",
    "flaky_test",
    "gotcha",
)
# proposed: an agent's claim, not yet backed by the controller; verified: backed by evidence and
# every source file still has the content it had; stale: a source changed (never shown).
FactStatus = Literal["proposed", "verified", "stale"]

MAX_TEXT_CHARS = 300
MAX_COMMAND_CHARS = 200
MAX_SOURCES = 6
MAX_SOURCE_BYTES = 2_000_000
# Files that hold secrets or are not the repository's to describe.
FORBIDDEN_SOURCE_PARTS = frozenset({CONTROLLER_DIRECTORY, ".git", ".ssh", ".aws", ".gnupg"})
SECRET_FILE_NAME = re.compile(r"(^\.env(\..*)?$)|\.(pem|key|p12|pfx|keystore)$|^id_(rsa|ed25519)")

# Shapes of credentials; a fact that contains one is refused, not redacted (a redacted fact is
# still a fact somebody pasted a secret into).
SECRET_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(
        r"(?i)\b[\w-]*(?:api[_-]?key|secret|token|passwd|password|credential)[\w-]*"
        r"\s*[:=]\s*['\"]?[^\s'\"]{6,}"
    ),
    re.compile(r"://[^/\s:@]+:[^/\s@]{3,}@"),  # user:password@host
)
MIN_SECRET_LENGTH = 8


class Evidence(BaseModel):
    """What backs a fact: the run, and the controller records inside it (check ids of the
    verifier; sequence numbers of tool calls in the run's ``trajectory.jsonl``)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = ""
    check_ids: list[str] = Field(default_factory=list)
    tool_seqs: list[int] = Field(default_factory=list)


class Source(BaseModel):
    """A file a fact depends on and its content hash when the fact was recorded."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str


class Fact(BaseModel):
    """One remembered fact. ``topic`` names what it is about (one fact per topic: a newer
    observation of the same topic replaces the old one); ``id`` derives from kind and topic."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: FactKind
    topic: str
    text: str
    status: FactStatus = "verified"
    command: str = ""  # a proposal's command, which the controller looks for in the run
    evidence: Evidence = Field(default_factory=Evidence)
    sources: list[Source] = Field(default_factory=list)
    revision: str = ""  # the workspace tree hash the fact was observed on
    created: datetime = Field(default_factory=utc_now)
    last_used: datetime | None = None
    proposed_by: str = ""  # the teammate that proposed it, if an agent did

    @property
    def usable(self) -> bool:
        """Verified and not stale: the only facts that reach a prompt."""

        return self.status == "verified"


@dataclass(frozen=True)
class SeenCheck:
    """A check the controller ran this run: what confirms a proposal about a command."""

    id: str
    kind: str
    command: str
    directory: str
    status: str


def fact_id(kind: str, topic: str) -> str:
    digest = hashlib.sha256("\0".join((kind, topic)).encode()).hexdigest()
    return f"{kind}-{digest[:8]}"


def one_line(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Whitespace collapsed to single spaces and cut to ``limit`` characters."""

    squeezed = " ".join(text.split())
    return squeezed if len(squeezed) <= limit else squeezed[: limit - 3].rstrip() + "..."


def secret_in(text: str, secrets: tuple[str, ...] = ()) -> str | None:
    """Why ``text`` looks like it contains a secret (``None`` if it does not): a known secret
    value of this environment, or the shape of a credential."""

    for value in secrets:
        if len(value) >= MIN_SECRET_LENGTH and value in text:
            return "it contains a secret value of this environment"
    for shape in SECRET_SHAPES:
        if shape.search(text):
            return "it looks like it contains a credential"
    return None


def source_problem(path: str) -> str | None:
    """Why ``path`` cannot be a fact's source (``None`` if it can)."""

    parts = Path(path).parts
    if not path.strip() or Path(path).is_absolute() or ".." in parts:
        return f"{path!r} is not a path inside the project; use a relative path"
    if {part.lower() for part in parts} & FORBIDDEN_SOURCE_PARTS:
        return f"{path!r} is not part of the project's own files"
    if SECRET_FILE_NAME.search(Path(path).name):
        return f"{path!r} may hold secrets and cannot be a source"
    return None


def hash_file(path: Path) -> str | None:
    """SHA-256 of a regular file's bytes; ``None`` if it is missing, too large or unreadable."""

    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def hash_sources(root: Path, paths: list[str]) -> list[Source] | str:
    """The sources for ``paths`` (relative to ``root``), or one line saying what is wrong."""

    if len(paths) > MAX_SOURCES:
        return f"at most {MAX_SOURCES} source paths per fact, got {len(paths)}"
    sources: list[Source] = []
    for name in paths:
        if (problem := source_problem(name)) is not None:
            return problem
        digest = hash_file(root / name)
        if digest is None:
            return f"{name!r} is not a readable file in the project (or is over 2 MB)"
        sources.append(Source(path=Path(name).as_posix(), sha256=digest))
    return sources
