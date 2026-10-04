"""Read-only access to a run's project files.

The same boundary as the agents' file tools (``ProjectWorkspace.resolve``: nothing outside the
project, nothing in ``.git``, nothing in the controller's state directory, symlinks checked after
they are resolved), and on top of it files that look like secrets are never listed or returned.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from engineering_team.tools.workspace import (
    IGNORED_LIST_DIRECTORIES,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    ProjectWorkspace,
    WorkspaceError,
)
from engineering_team.ui.state import state_of

router = APIRouter()

SECRET_NAME = re.compile(
    r"""^(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|\.git-credentials|id_(rsa|dsa|ecdsa|ed25519).*|
    .*\.(pem|key|p12|pfx|jks|keystore|kdbx)|credentials(\..*)?|.*secrets?\.(json|ya?ml|toml|env))$""",
    re.IGNORECASE | re.VERBOSE,
)
SECRET_EXAMPLES = re.compile(r"^\.env\.(example|sample|template|dist)$", re.IGNORECASE)
SECRET_DIRECTORIES = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker"})


class Entry(BaseModel):
    name: str
    path: str
    type: str  # file | directory
    size: int | None = None


class FileContent(BaseModel):
    path: str
    size: int
    content: str
    truncated: bool = False


def is_secret(parts: tuple[str, ...]) -> bool:
    """Whether a project path looks like a credential: ``.env``, keys, ``.ssh/``, ..."""

    if any(part.lower() in SECRET_DIRECTORIES for part in parts[:-1]):
        return True
    if parts and parts[-1].lower() in SECRET_DIRECTORIES:
        return True
    name = parts[-1] if parts else ""
    return bool(SECRET_NAME.match(name)) and not SECRET_EXAMPLES.match(name)


def _workspace(request: Request, run_id: str) -> ProjectWorkspace:
    ref = state_of(request).need(run_id)
    return ProjectWorkspace.create(ref.workspace)


def _resolve(workspace: ProjectWorkspace, path: str) -> Path:
    try:
        target = workspace.resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(
            403 if "not accessible" in str(exc) or ".git" in str(exc) else 404, str(exc)
        ) from exc
    relative = target.relative_to(workspace.root)
    if is_secret(relative.parts):
        raise HTTPException(403, "That file looks like a credential and is never served.")
    return target


@router.get("/runs/{run_id}/files")
def list_files(request: Request, run_id: str, path: str = ".") -> list[Entry]:
    workspace = _workspace(request, run_id)
    base = _resolve(workspace, path)
    if not base.is_dir():
        raise HTTPException(422, f"{path!r} is a file; read it at /files/{path}.")
    entries: list[Entry] = []
    with os.scandir(base) as scan:
        for item in sorted(scan, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name)):
            relative = (base / item.name).relative_to(workspace.root)
            if item.is_symlink() or is_secret(relative.parts):
                continue
            if item.is_dir() and item.name in IGNORED_LIST_DIRECTORIES:
                continue
            is_dir = item.is_dir(follow_symlinks=False)
            entries.append(
                Entry(
                    name=item.name,
                    path=relative.as_posix(),
                    type="directory" if is_dir else "file",
                    size=None if is_dir else item.stat(follow_symlinks=False).st_size,
                )
            )
            if len(entries) >= MAX_LIST_ENTRIES:
                break
    return entries


@router.get("/runs/{run_id}/files/{path:path}")
def read_file(request: Request, run_id: str, path: str) -> FileContent:
    workspace = _workspace(request, run_id)
    target = _resolve(workspace, path)
    if not target.is_file():
        raise HTTPException(422, f"{path!r} is a directory; list it at /files?path={path}.")
    size = target.stat().st_size
    with target.open("rb") as handle:
        raw = handle.read(MAX_READ_BYTES)
    if b"\x00" in raw[:8000]:
        raise HTTPException(415, "Binary files are not served.")
    return FileContent(
        path=target.relative_to(workspace.root).as_posix(),
        size=size,
        content=raw.decode("utf-8", errors="replace"),
        truncated=size > MAX_READ_BYTES,
    )


__all__ = ["router", "is_secret", "Any"]
