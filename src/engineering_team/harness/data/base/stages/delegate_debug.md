---
expected_output: >
  The diagnosis (root cause, file and line, the evidence you saw), what you changed if
  anything, and the commands you ran with their exit codes.
---
The lead of {project_name} in {workspace_path} could not make a check pass and asks you to
find out why.

Request the team is working on:

{requirements}

{brief}

Run the check, read the failure and the code it points to, and find the root cause; name the
file and line. If the brief lets you change paths, make the smallest fix there and run the
check again; otherwise change nothing. Never report a check as passing unless you ran it.

{handoff}
{card}
{notes}
{resume_note}
