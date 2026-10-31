---
expected_output: >
  The refactor corrected so the checks pass, and a short summary of what you undid or changed.
---
The controller ran its own independent checks after a behaviour-preserving refactor of
{project_name} in {workspace_path}, and some required ones failed (a test, or a policy about
the diff). {{completion}}

What failed:

{failures}

A test that passed before the refactor and fails now means the refactor changed behaviour:
undo or correct the refactor, never the test (existing tests may not be edited, deleted or
skipped). If a policy names files (a test, a manifest, too many lines), revert them or shrink
the change. Run the failing command again before you finish and reply with a short summary.

{handoff}
{card}
{notes}
{resume_note}
