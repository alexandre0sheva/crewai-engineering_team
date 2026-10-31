---
expected_output: >
  The confirmed findings fixed in the workspace, and a short summary per finding id.
---
Independent reviewers read {project_name} in {workspace_path} and found problems the
controller wants fixed. What you report does not change the outcome: the controller runs
its checks again when you finish, and only passing checks count.

Specification:

{spec}

Plan:

{plan}

Review findings to fix:

{findings}

For each finding, confirm it in the code first (a finding can be wrong; if it is, leave
the code alone and say why), then make the smallest correct fix. Never weaken, delete, skip,
or special-case a test or a check to get it green, and do not edit docs/verification.md or
docs/review.md (the controller writes them). Run the project's tests again before you
finish. Reply with a short summary: for each finding id, fixed or not and why, and the files
you changed. The controller records it in docs/qa-notes.md; do not write that file yourself.

{handoff}
{card}
{notes}
{resume_note}
