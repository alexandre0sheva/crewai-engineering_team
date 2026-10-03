"""Checks that a stage's promised files exist and hold real content."""

from __future__ import annotations

from engineering_team.tools.workspace import ProjectWorkspace

MIN_ARTIFACT_CHARACTERS = 40


def missing_artifacts(workspace: ProjectWorkspace, *relative_paths: str) -> list[str]:
    """Describe each path that is absent or nearly empty, e.g. ``docs/x.md (missing)``.

    This only rejects absent or near-empty files; it does not prove the work is correct.
    """

    problems: list[str] = []
    for relative_path in relative_paths:
        path = workspace.resolve(relative_path)
        if not path.is_file():
            problems.append(f"{relative_path} (missing)")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if len("".join(content.split())) < MIN_ARTIFACT_CHARACTERS:
            problems.append(f"{relative_path} (nearly empty)")
    return problems
