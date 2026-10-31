---
expected_output: >
  Accurate documentation files in the workspace and a short summary: the files written or
  changed, what was verified by running it, and what is unchecked.
---
Document the existing code of {project_name} in {workspace_path}. You may write documentation
files only (Markdown, reStructuredText, the docs directory, the README and CHANGELOG); the
file tools refuse anything else and the controller checks the diff. Do not change code,
including comments and docstrings.

The request (what to document, if the person said):

{requirements}

{context}

Read the code, its tests, and the existing documentation first (the codebase map above is a
guide, not evidence: confirm it in the code). Then write what a new maintainer needs and the
project lacks: what it is and who uses it; how to set it up, run, and test it (use the
commands that really work; run them); the architecture and the main flows with real module
and function names; configuration and environment variables the code reads; data formats and
external services; conventions to follow; known quirks and risks. Update wrong or stale
existing documentation instead of adding a second version. Mark anything you could not verify
as unchecked and never describe behaviour you have not read in the code.

{handoff}
{card}
{notes}
{resume_note}
