"""The workspace revision a verification is *about*.

Files the controller writes about the verification itself (its report), and the documents
agents write to narrate (QA notes, the release report), are not part of what was verified, so
they do not move the revision: writing the report must not make its own evidence stale. Any
other change does.
"""

from __future__ import annotations

from engineering_team.runtime.snapshot import workspace_revision
from engineering_team.tools.workspace import ProjectWorkspace

REPORT_FILE = "docs/verification.md"  # written by the controller from the check results
NOTES_FILE = "docs/qa-notes.md"  # the quality agent's narrative; never evidence
RELEASE_FILE = "docs/release-report.md"  # the release stage's write-up, a document
UNTRACKED = (REPORT_FILE, NOTES_FILE, RELEASE_FILE)


def verification_revision(workspace: ProjectWorkspace) -> str:
    return workspace_revision(workspace, exclude=UNTRACKED)
