---
expected_output: >
  docs/architecture.md written in the workspace, and a JSON plan (stack, commands,
  work_packages, risks).
---
Design the solution for {project_name} and split the work. Inspect the current project
tree at {workspace_path}; reuse coherent existing code.

The specification below is the contract: design for its acceptance criteria and non-goals.
The original request is given only for context; where it disagrees with the specification,
follow the specification.

Specification:

{spec}

Original request:

{requirements}

{context}

Teammates available for work packages (a package's role is a teammate's key, or its short
form without "_engineer"; a role nobody has goes to the first teammate listed):

{teammates}

Choose the smallest suitable stack (do not force Python, a web UI, or any framework the
specification does not support) and prefer one working vertical slice over breadth. Write
docs/architecture.md (goals and non-goals, stack and why, project tree, data model and
contracts, flows, security boundaries, failure behaviour). Then return the plan: the stack;
the exact project commands (setup, test, lint, build, run) as lists of command lines; and
work packages. Each work package has an id (WP-1, ...), a title, a role ("backend",
"frontend", ...; only create a frontend package if the product has a user interface), a
description, the paths it owns (gitignore-style globs, for example "src/api/**"), the ids
of the packages it depends on, and the acceptance criteria ids it delivers (existing ids
from the specification; every package delivers at least one, and every criterion is
delivered by some package). Packages that do not depend on each other run at the same time
and may change only the paths they own, so give each package its own directories (tests
belong to the package they test) and make owned paths disjoint. Never give a package a
shared root file such as README.md, package.json, pyproject.toml, a lockfile, or CI
configuration: the foundation stage owns those and an integration stage runs afterwards to
reconcile the packages. Mark a package "required": false only if the product works without
it. Also list the main risks.

{handoff}
{card}
{notes}
{resume_note}
