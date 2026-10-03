"""Finding runs without knowing which project they belong to.

Every command that takes a run id works on ``<workspace_root>/<project>/.engineering-team/runs/``.
Passing ``--project-name`` narrows the search to one project; otherwise every project under the
workspace root is searched, so ``engineering-team status 20261003-101500-ab12cd`` is enough.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from engineering_team.contracts import RunManifest
from engineering_team.runtime.run_store import RunNotFound, RunStore
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY
from engineering_team.workspaces import resolve_workspace_root, slugify_project_name

REGISTRY = ".external-workspaces.json"


@dataclass(frozen=True)
class RunRef:
    """A run, the project directory it belongs to, and its manifest as read."""

    project: str
    workspace: Path
    manifest: RunManifest

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    @property
    def store(self) -> RunStore:
        return RunStore(self.workspace)

    @property
    def run_dir(self) -> Path:
        return self.store.run_dir(self.run_id)


def project_directories(workspace_root: str | Path) -> list[Path]:
    """Project directories under the workspace root that hold at least one run, sorted by name."""

    root = resolve_workspace_root(workspace_root)
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.iterdir()
        if entry.is_dir() and (entry / CONTROLLER_DIRECTORY / "runs").is_dir()
    )


def register_workspace(workspace_root: str | Path, workspace: Path, project: str) -> None:
    """Remember a workspace that is not under the workspace root (a branch or worktree of the
    user's own repository), so ``status``, ``board``, ``cancel``, ``diff`` and the rest find its
    runs. ``project`` is how ``runs`` names it."""

    root = resolve_workspace_root(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    entries = _registered(root)
    path = str(workspace.resolve())
    entries = [entry for entry in entries if entry["path"] != path]
    entries.append({"path": path, "project": project})
    (root / REGISTRY).write_text(json.dumps({"workspaces": entries}, indent=2) + "\n", "utf-8")


def _registered(root: Path) -> list[dict[str, str]]:
    try:
        data = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("workspaces") if isinstance(data, dict) else None
    return [
        {"path": str(e["path"]), "project": str(e.get("project") or Path(str(e["path"])).name)}
        for e in entries or []
        if isinstance(e, dict) and "path" in e
    ]


def find_runs(workspace_root: str | Path, project: str | None = None) -> list[RunRef]:
    """Every readable run (of one project, or of all), oldest first. Workspaces registered with
    :func:`register_workspace` count as projects too."""

    root = resolve_workspace_root(workspace_root)
    wanted = slugify_project_name(project) if project is not None else None
    labelled: dict[Path, str] = {}
    for directory in project_directories(workspace_root):
        if wanted is None or directory.name == wanted:
            labelled[directory.resolve()] = directory.name
    for entry in _registered(root):
        directory = Path(entry["path"])
        if not (directory / CONTROLLER_DIRECTORY / "runs").is_dir():
            continue
        try:
            matches = wanted is None or slugify_project_name(entry["project"]) == wanted
        except ValueError:
            matches = False
        if matches:
            labelled.setdefault(directory.resolve(), entry["project"])
    found = [
        RunRef(label, directory, manifest)
        for directory, label in labelled.items()
        for manifest in RunStore(directory).list_runs()
    ]
    return sorted(found, key=lambda ref: (ref.manifest.created, ref.run_id))


def locate_run(
    workspace_root: str | Path, run_id: str | None = None, project: str | None = None
) -> RunRef:
    """The run named by ``run_id`` (an unambiguous prefix is enough), or the latest one.

    Raises :class:`~engineering_team.runtime.run_store.RunNotFound` (a ``ValueError``, so a usage
    error) when there is none or the prefix matches several.
    """

    runs = find_runs(workspace_root, project)
    where = f"project {project!r}" if project else f"{resolve_workspace_root(workspace_root)}"
    if not runs:
        raise RunNotFound(
            f"No runs found in {where}. Start one with: engineering-team new --example tiny-notes"
        )
    if run_id is None:
        return runs[-1]
    exact = [ref for ref in runs if ref.run_id == run_id]
    matches = exact or [ref for ref in runs if ref.run_id.startswith(run_id)]
    if not matches:
        raise RunNotFound(f"No run {run_id!r} in {where}. List runs with: engineering-team runs")
    if len(matches) > 1:
        ids = ", ".join(ref.run_id for ref in matches[:5])
        raise RunNotFound(f"{run_id!r} matches several runs ({ids}); give more of the id.")
    return matches[0]
