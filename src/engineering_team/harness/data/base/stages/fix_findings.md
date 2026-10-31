---
expected_output: >
  The confirmed findings fixed in the workspace and a short summary per finding id.
---
Fix the serious security findings in the existing project {project_name} in {workspace_path}
with the smallest correct changes.

The findings (from the audit; each is a claim, confirm it in the code first):

{findings}

For each finding: confirm it in the code (leave a wrong finding alone and say why), fix the
cause, and keep the diff minimal and in the project's style. For a vulnerable dependency,
edit the manifest to the fixed version only if the project's tests can tell it still works;
otherwise say that it needs a human. Never weaken, skip, or delete a test. Run the project's
tests for what you changed. Reply with a short summary: for each finding id, fixed or not
and why, and the files you changed.

{handoff}
{card}
{notes}
{resume_note}
