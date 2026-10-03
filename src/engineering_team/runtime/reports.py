"""Where the controller's own write-ups go: in the project's ``docs/`` or in the run directory.

A project the team builds from nothing keeps ``docs/verification.md``, ``docs/review.md``,
``docs/spec.md`` and ``docs/qa-notes.md`` as part of the deliverable. When the team changes
someone else's project (the repository modes) those files must not end up in the diff, so they go
to ``<run dir>/reports/`` instead. Everything that writes or names one of them asks a
:class:`Reports` for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering_team.atomic_io import atomic_write_text
from engineering_team.tools.workspace import ProjectWorkspace

SPEC = "spec.md"
VERIFICATION = "verification.md"
REVIEW = "review.md"
QA_NOTES = "qa-notes.md"


@dataclass(frozen=True)
class Reports:
    """The controller's report files for one run. ``directory`` set: they live there (outside
    the project); ``None``: in the project's ``docs/``."""

    workspace: ProjectWorkspace
    directory: Path | None = None

    @classmethod
    def in_run_dir(cls, workspace: ProjectWorkspace, run_dir: Path) -> Reports:
        return cls(workspace, run_dir / "reports")

    @property
    def in_project(self) -> bool:
        return self.directory is None

    def label(self, name: str) -> str:
        """How a message names the file: its project path, or its name in the run's reports."""

        return f"docs/{name}" if self.in_project else f"reports/{name} (in the run directory)"

    def path(self, name: str) -> Path:
        base = self.workspace.root / "docs" if self.directory is None else self.directory
        return base / name

    def write(self, name: str, text: str) -> None:
        if self.directory is None:
            self.workspace.write_file(f"docs/{name}", text)
        else:
            atomic_write_text(self.path(name), text)

    def read(self, name: str) -> str:
        """The file's text; ``FileNotFoundError`` when it does not exist."""

        return self.path(name).read_text(encoding="utf-8")
