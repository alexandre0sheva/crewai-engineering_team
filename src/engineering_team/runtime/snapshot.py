"""A snapshot of the workspace taken when a run starts, so changes can be shown as a diff."""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from engineering_team.tools.ignore import IgnoreRules, iter_files, looks_binary
from engineering_team.tools.workspace import ProjectWorkspace

# Content is kept only for files small enough to diff, and only up to a total budget; larger
# files are still tracked by hash so "modified" is reported even when no diff can be shown.
MAX_STORED_FILE_BYTES = 200_000
MAX_STORED_TOTAL_BYTES = 20_000_000


@dataclass(frozen=True)
class FileState:
    sha256: str
    size: int
    text: str | None = None  # None: binary, too large, or over the snapshot budget


@dataclass(frozen=True)
class Snapshot:
    """Relative POSIX path -> state, for every non-ignored regular file."""

    files: Mapping[str, FileState] = field(default_factory=dict)

    @classmethod
    def take(cls, workspace: ProjectWorkspace) -> Snapshot:
        files: dict[str, FileState] = {}
        stored = 0
        for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            text = None
            if (
                len(data) <= MAX_STORED_FILE_BYTES
                and stored + len(data) <= MAX_STORED_TOTAL_BYTES
                and not looks_binary(path)
            ):
                text = data.decode("utf-8", errors="replace")
                stored += len(data)
            files[workspace.relative_name(path)] = FileState(
                hashlib.sha256(data).hexdigest(), len(data), text
            )
        return cls(files)


def workspace_revision(workspace: ProjectWorkspace, exclude: Collection[str] = ()) -> str:
    """A hash of every non-ignored regular file's path and content: the workspace's revision.

    The same tree always hashes the same; any added, removed, renamed, or edited file changes
    it. Ignored paths (``.gitignore``, heavy directories, ``.engineering-team``) do not count,
    so a run's own state files never move it. Contents are streamed, not held in memory.
    ``exclude`` lists project-relative paths that do not count (the verifier's own report).
    """

    digest = hashlib.sha256()
    skipped = set(exclude)
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        if skipped and workspace.relative_name(path) in skipped:
            continue
        inner = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    inner.update(chunk)
        except OSError:
            continue  # vanished or unreadable mid-walk: indistinguishable from absent
        digest.update(workspace.relative_name(path).encode("utf-8", errors="replace"))
        digest.update(b"\0" + inner.digest())
    return digest.hexdigest()[:32]
