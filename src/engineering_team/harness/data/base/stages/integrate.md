---
expected_output: >
  A project whose packages work together, and docs/integration.md recording the commands run
  and every change made.
---
Integrate the work packages of {project_name} in {workspace_path}. They were built in
parallel, each by an agent that could change only its own paths, so they may disagree where
they meet (interfaces, imports, configuration, shared files).

Specification:

{spec}

Plan:

{plan}

Work package results (status, summary, error):

{packages}

Read what each package delivered, then build the whole project and run its tests, lint, and
type checks. Fix cross-package contract mismatches and anything a skipped or failed
optional package left dangling; you may change any file, including the shared files
(README.md, dependency manifests, CI configuration). Keep fixes minimal and do not
rewrite a package's design. Write docs/integration.md listing what you ran with exit
statuses, every change you made and why, and any package that is missing or incomplete.

{handoff}
{card}
{notes}
{resume_note}
