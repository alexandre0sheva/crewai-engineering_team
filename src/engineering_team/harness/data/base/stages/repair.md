---
expected_output: >
  The failing checks fixed in the workspace, and a short summary of the root cause, the files
  changed, and what is still failing.
---
The controller ran its own independent checks on {project_name} in {workspace_path} and
some required ones failed. You did not run them, and what you report does not change the
outcome: the controller runs the same checks again when you finish, and only passing checks
count.

Specification:

{spec}

Plan:

{plan}

What failed:

{failures}

Find the cause of each failure (read the failing code and tests; run the check's command
yourself to reproduce it) and fix the code. Never weaken, delete, skip, or special-case a
test or a check to get it green, and do not edit docs/verification.md (the controller writes
it). Keep the changes minimal and in scope; if a failure shows the specification itself is
wrong, say so instead of bending the code. Run the failing command again before you finish.
Reply with a short summary: the root cause, the files you changed, and anything you could
not fix. The controller records it in docs/qa-notes.md; do not write that file yourself.

{handoff}
{card}
{notes}
{resume_note}
