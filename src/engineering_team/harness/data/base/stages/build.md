---
expected_output: >
  A working, tested project with README.md and docs/release-report.md, and a summary of what
  was built, what was verified, and what is missing.
---
Build {project_name} end to end in {workspace_path}, on your own.

Request:

{requirements}

Choose the smallest suitable stack, design a minimal architecture, implement a working
vertical slice with focused automated tests, run the tests and a build or startup smoke
check, fix what fails, and write a README.md (setup, run, test) plus docs/release-report.md
(delivered scope, how it was verified, known limitations). Inspect the existing workspace
first and reuse coherent code. Never report a check as passing unless you ran it.

{handoff}
{card}
{notes}
{resume_note}
