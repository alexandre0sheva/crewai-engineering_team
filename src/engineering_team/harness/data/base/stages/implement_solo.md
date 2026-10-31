---
expected_output: >
  The project built in the workspace with tests and README.md. Report the criteria delivered,
  files changed, commands run with their exit codes, and any honest limitation.
---
Build {project_name} in {workspace_path}: the whole plan is yours, in one session, and
nobody else changes the project while you work.

Specification:

{spec}

Plan:

{plan}

Your work package (the whole plan):

{package}

{context}

Read what exists first and reuse coherent code. Set up a conventional structure for the
stack (dependency and tool configuration, a safe .gitignore, .env.example when
configuration is needed), implement real end to end behaviour for every acceptance
criterion with focused tests next to the code, validate inputs and error paths, keep
secrets out of the repository, and write a README.md (setup, run, test). Install only
necessary dependencies. Run the tests and the cheapest build or start-up check, fix what
fails, and never report a check as passing unless you ran it. You may change any file.

{handoff}
{card}
{notes}
{resume_note}
