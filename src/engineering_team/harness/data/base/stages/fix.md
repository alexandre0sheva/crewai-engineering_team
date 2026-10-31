---
expected_output: >
  The bug fixed in the workspace, and a JSON note (root_cause, change, risk, files).
---
Fix the bug in the existing project {project_name} in {workspace_path} with the smallest
correct change.

The bug report:

{requirements}

{bug}

Triage (the debugger's earlier reading, not evidence; "(not available)" when the task was
small enough to skip it, so find the cause yourself first):

{triage}

The reproduction (the controller ran it and saw it fail before any change):

{repro}

{context}

Find the root cause (reproduce it yourself first: run the reproduction command and read the
failure), then fix the cause, not the symptom. Follow the conventions of the code around it
and keep the diff minimal: change only what the cause needs, do not reformat, rename, or tidy
unrelated code. The reproduction files are read-only for you and the controller checks them:
never edit, weaken, skip, or delete a test to make it pass, and do not edit
the controller's reports. The controller will run the same command again and require it to
pass, together with the project's own checks (which must show no new failure); failures the
project had before are not yours to fix. Run the reproduction and the nearby tests yourself
before you finish.

Return a JSON note: the root cause (one or two sentences, in terms of the code), what you
changed and why that fixes it, the risk (what else this change could affect and what is not
tested), and the files you changed.

{handoff}
{card}
{notes}
{resume_note}
