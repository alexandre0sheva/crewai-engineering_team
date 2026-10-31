---
expected_output: >
  The failing checks fixed by reverting or correcting manifest edits, and a short summary.
---
The controller ran its own independent checks on the upgraded project {project_name} in
{workspace_path}, and some required ones failed (usually a policy: a file that is not a
manifest or lockfile changed). {{completion}} You may change manifests and lockfiles only.

What failed:

{failures}

Revert what the policy lists. If a manifest edit itself is the cause, restore the previous
version of that dependency. Reply with a short summary.

{handoff}
{card}
{notes}
{resume_note}
