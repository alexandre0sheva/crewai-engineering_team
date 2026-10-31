---
expected_output: >
  The step done in the workspace, and a short summary of what was done, which files changed, and
  what is left.
---
Do one step of a task on the existing project {project_name} in {workspace_path}. The task
for this step is written by whoever set up the recipe:

{instructions}

The request this run was started with:

{requirements}

{context}

Read the code you will touch first and follow the conventions you find. Keep the change as
small as the step needs, never weaken, delete, or skip a test to get a check green, and run
the project's own checks for what you changed. Reply with a short summary: what you did, the
files you changed, and what you could not do.

{handoff}
{card}
{notes}
{resume_note}
