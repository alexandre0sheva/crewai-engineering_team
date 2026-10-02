"""Creating, owning, and safely resetting persistent project directories."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.tools.workspace import ProjectWorkspace

DEFAULT_WORKSPACE_ROOT = "workspace"
STATE_DIRECTORY = ".engineering-team"
OWNER_MARKER = Path(STATE_DIRECTORY) / "owner.json"
# 0.1.0 wrote run.json into every project it created; treat those as ours and upgrade them.
LEGACY_MARKER = Path(STATE_DIRECTORY) / "run.json"
OWNER_TOOL = "engineering-team"


def slugify_project_name(value: str) -> str:
    """Convert a display name into a safe workspace directory name."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Project name must contain at least one letter or number.")
    return slug[:80]


def resolve_workspace_root(workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT) -> Path:
    """Resolve the parent directory of generated projects against the caller's cwd."""

    root = Path(workspace_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError("The filesystem root cannot be used as ENGINEERING_WORKSPACE_ROOT.")
    return root


def _package_version() -> str:
    try:
        return metadata.version("engineering_team")
    except metadata.PackageNotFoundError:  # running from an unbuilt source tree
        return "unknown"


def _read_owner(project_path: Path) -> dict | None:
    marker = project_path / OWNER_MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("tool") == OWNER_TOOL else None


def _write_owner_marker(project_path: Path) -> None:
    marker = project_path / OWNER_MARKER
    if _read_owner(project_path) is not None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "tool": OWNER_TOOL,
                "version": _package_version(),
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _is_ours(project_path: Path) -> bool:
    return _read_owner(project_path) is not None or (project_path / LEGACY_MARKER).is_file()


def reset_refusal(
    project_path: Path,
    *,
    cwd: Path,
    home: Path,
    package_file: Path,
) -> str | None:
    """Return why deleting ``project_path`` is never allowed, or ``None``.

    These refusals cannot be overridden: they protect the filesystem root, the user's home
    directory (and its ancestors), the directory being worked in (and its ancestors), the
    orchestrator's own installation, and symlinks whose target is unknown.
    """

    if project_path.is_symlink():
        return "it is a symbolic link"
    if project_path == Path(project_path.anchor) or project_path.parent == project_path:
        return "it is the filesystem root"
    if project_path == home or project_path in home.parents:
        return "it is your home directory or one of its parents"
    if project_path == cwd or project_path in cwd.parents:
        return "it is the current working directory or one of its parents"
    if package_file.is_relative_to(project_path):
        return "it contains the engineering-team installation"
    return None


def prepare_workspace(
    project_name: str,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
    *,
    reset: bool = False,
    force_reset: bool = False,
    command_allowlist: Iterable[str] = (),
    subprocess_env_allowlist: Iterable[str] = (),
) -> ProjectWorkspace:
    """Create or resume one persistent project directory.

    A directory is *owned* when ``.engineering-team/owner.json`` exists. Existing non-empty
    directories that were not created by this tool are never written to, and ``reset`` only
    deletes owned directories unless ``force_reset`` is given. Dangerous targets are refused
    even then (see :func:`reset_refusal`).
    """

    root = resolve_workspace_root(workspace_root)
    project_path = root / slugify_project_name(project_name)

    if project_path.is_symlink():
        raise ValueError(f"Project directory is a symbolic link and cannot be used: {project_path}")
    if project_path.exists() and not project_path.is_dir():
        raise ValueError(f"Project path exists and is not a directory: {project_path}")

    exists = project_path.is_dir()
    if exists and reset:
        refusal = reset_refusal(
            project_path,
            cwd=Path.cwd().resolve(),
            home=Path.home().resolve(),
            package_file=Path(__file__).resolve(),
        )
        if refusal:
            raise ValueError(f"Refusing to reset {project_path}: {refusal}.")
        if not _is_ours(project_path) and not force_reset:
            raise ValueError(
                f"Refusing to reset {project_path}: it was not created by engineering-team. "
                "Check the path, then pass --force-reset to delete it anyway."
            )
        # Never delete a workspace another run is writing to. Taking and dropping the lock
        # raises WorkspaceBusy if one is held; the next run takes it for real afterwards.
        WorkspaceLock(project_path).acquire("reset").release()
        shutil.rmtree(project_path)
        exists = False
    elif exists and not _is_ours(project_path) and any(project_path.iterdir()):
        raise ValueError(
            f"{project_path} already exists, is not empty, and was not created by "
            "engineering-team, so it will not be modified. Choose another --project-name "
            "or an empty directory. (Working on existing projects is not supported yet.)"
        )

    workspace = ProjectWorkspace.create(
        project_path,
        extra_commands=command_allowlist,
        env_passthrough=subprocess_env_allowlist,
    )
    _write_owner_marker(workspace.root)
    return workspace
