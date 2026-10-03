"""Finding runs without knowing which project they belong to.

Every command that takes a run id works on ``<workspace_root>/<project>/.engineering-team/runs/``.
Passing ``--project-name`` narrows the search to one project; otherwise every project under the
workspace root is searched, so ``engineering-team status 20261003-101500-ab12cd`` is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering_team.contracts import RunManifest
from engineering_team.runtime.run_store import RunNotFound, RunStore
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY
from engineering_team.workspaces import resolve_workspace_root, slugify_project_name


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


def find_runs(workspace_root: str | Path, project: str | None = None) -> list[RunRef]:
    """Every readable run (of one project, or of all), oldest first."""

    if project is not None:
        directory = resolve_workspace_root(workspace_root) / slugify_project_name(project)
        directories = [directory] if directory.is_dir() else []
    else:
        directories = project_directories(workspace_root)
    found = [
        RunRef(directory.name, directory, manifest)
        for directory in directories
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
