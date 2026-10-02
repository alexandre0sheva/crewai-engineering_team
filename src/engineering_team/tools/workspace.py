"""The project workspace: a persistent, project-scoped filesystem boundary.

This is a safety boundary, not a virtual machine. File APIs reject traversal and
symlink escapes. Command execution lives in :mod:`engineering_team.tools.commands`;
commands can still execute code written inside the project, so use an OS or container
sandbox as an additional layer when running untrusted requirements.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 250_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 500

GIT_DIRECTORY = ".git"
CONTROLLER_DIRECTORY = ".engineering-team"
# The only part of the controller directory agents may touch: scratch space that project
# commands already use as TMPDIR.
AGENT_SCRATCH_PARTS = (CONTROLLER_DIRECTORY, "tmp")

IGNORED_LIST_DIRECTORIES = {
    ".git",
    CONTROLLER_DIRECTORY,
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}


class WorkspaceError(ValueError):
    """Raised when an operation would leave the configured project workspace."""


@dataclass(frozen=True)
class ProjectWorkspace:
    """A persistent filesystem root used by one generated application."""

    root: Path
    # Executables allowed in addition to the defaults, and environment variable names that
    # project commands may inherit. Both come from Settings; the workspace never reads the
    # process environment configuration itself.
    extra_commands: frozenset[str] = frozenset()
    env_passthrough: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        extra_commands: Iterable[str] = (),
        env_passthrough: Iterable[str] = (),
    ) -> ProjectWorkspace:
        resolved = Path(root).expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise WorkspaceError("The filesystem root cannot be used as a project workspace.")
        resolved.mkdir(parents=True, exist_ok=True)
        return cls(
            root=resolved,
            extra_commands=frozenset(
                item.strip().lower() for item in extra_commands if item.strip()
            ),
            env_passthrough=frozenset(item.strip() for item in env_passthrough if item.strip()),
        )

    def resolve(
        self, relative_path: str, *, must_exist: bool = False, readonly: bool = False
    ) -> Path:
        """Resolve an agent-supplied path inside the workspace.

        ``readonly=True`` additionally allows command logs under
        ``.engineering-team/runs/<id>/commands/`` so agents can page through full output.
        """

        if not isinstance(relative_path, str):
            raise WorkspaceError("Paths must be strings relative to the project workspace.")

        normalized = relative_path.strip() or "."
        supplied = Path(normalized)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise WorkspaceError(f"Path must stay inside the project workspace: {relative_path}")
        self.reject_protected(supplied.parts, readonly=readonly)

        resolved = (self.root / supplied).resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(
                f"Path resolves outside the project workspace: {relative_path}"
            ) from exc
        # Check again after symlink resolution: an alias such as ``docs -> .git`` passes the
        # lexical check above but still lands in protected storage.
        self.reject_protected(relative.parts, readonly=readonly)

        if must_exist and not resolved.exists():
            raise WorkspaceError(f"Path does not exist: {relative_path}")
        return resolved

    @staticmethod
    def reject_protected(parts: tuple[str, ...], *, readonly: bool = False) -> None:
        """Reject paths in Git metadata or in the controller-owned state directory."""

        lowered = tuple(part.lower() for part in parts)  # case-insensitive file systems
        if GIT_DIRECTORY in lowered:
            raise WorkspaceError("Direct access to .git is not allowed.")
        if (
            readonly
            and len(lowered) == 5
            and lowered[:2] == (CONTROLLER_DIRECTORY, "runs")
            and lowered[3] == "commands"
        ):
            return  # a command log file: readable, never writable
        if lowered and lowered[0] == CONTROLLER_DIRECTORY and lowered[:2] != AGENT_SCRATCH_PARTS:
            raise WorkspaceError(
                f"{CONTROLLER_DIRECTORY}/ is managed by the orchestrator and is not accessible."
            )

    def relative_name(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        return "." if str(relative) == "." else relative.as_posix()

    def list_files(self, relative_path: str = ".", max_depth: int = 4) -> str:
        base = self.resolve(relative_path, must_exist=True)
        if not base.is_dir():
            raise WorkspaceError(f"Not a directory: {relative_path}")

        depth_limit = max(1, min(int(max_depth), 8))
        entries: list[str] = []
        base_depth = len(base.parts)

        for current_root, directory_names, file_names in os.walk(base):
            current = Path(current_root)
            depth = len(current.parts) - base_depth
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in IGNORED_LIST_DIRECTORIES and depth < depth_limit
            )

            for directory_name in directory_names:
                entries.append(f"{self.relative_name(current / directory_name)}/")
            for file_name in sorted(file_names):
                entries.append(self.relative_name(current / file_name))

            if len(entries) >= MAX_LIST_ENTRIES:
                entries = entries[:MAX_LIST_ENTRIES]
                entries.append(
                    f"... output limited to {MAX_LIST_ENTRIES} entries; narrow the path."
                )
                break

        return "\n".join(entries) if entries else "The requested directory is empty."

    def read_file(self, relative_path: str) -> str:
        path = self.resolve(relative_path, must_exist=True, readonly=True)
        if not path.is_file():
            raise WorkspaceError(f"Not a file: {relative_path}")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise WorkspaceError(f"File is {size} bytes; the read limit is {MAX_READ_BYTES} bytes.")

        content = path.read_bytes()
        if b"\x00" in content:
            raise WorkspaceError("Binary files cannot be read with this text tool.")
        return content.decode("utf-8")

    def write_file(self, relative_path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise WorkspaceError(
                f"Content is {len(encoded)} bytes; the write limit is {MAX_WRITE_BYTES} bytes."
            )

        path = self.resolve(relative_path)
        if path.exists() and path.is_dir():
            raise WorkspaceError(f"Cannot replace a directory with a file: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(encoded)} bytes to {self.relative_name(path)}."

    def replace_in_file(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
    ) -> str:
        if not old_text:
            raise WorkspaceError("old_text cannot be empty.")
        expected = int(expected_replacements)
        if expected < 1:
            raise WorkspaceError("expected_replacements must be at least 1.")

        current = self.read_file(relative_path)
        actual = current.count(old_text)
        if actual != expected:
            raise WorkspaceError(
                f"Expected {expected} occurrence(s) in {relative_path}, found {actual}; "
                "read the file again before editing."
            )
        updated = current.replace(old_text, new_text)
        return self.write_file(relative_path, updated)

    def delete_path(self, relative_path: str) -> str:
        supplied = Path(relative_path.strip())
        if not supplied.parts or str(supplied) == ".":
            raise WorkspaceError("The project workspace root cannot be deleted.")
        if supplied.is_absolute() or ".." in supplied.parts:
            raise WorkspaceError("Only paths inside the project workspace can be deleted.")
        self.reject_protected(supplied.parts)

        lexical_path = self.root / supplied
        if lexical_path.is_symlink():
            # Removing a link must not follow it, but its *parent* may itself be an alias for
            # protected storage, so resolve the parent and check where the link really lives.
            parent = self.resolve(supplied.parent.as_posix(), must_exist=True)
            self.reject_protected(parent.relative_to(self.root).parts)
            lexical_path.unlink()
            return f"Deleted symlink {supplied.as_posix()}."

        path = self.resolve(relative_path, must_exist=True)
        if path.is_dir():
            shutil.rmtree(path)
            kind = "directory"
        else:
            path.unlink()
            kind = "file"
        return f"Deleted {kind} {supplied.as_posix()}."
