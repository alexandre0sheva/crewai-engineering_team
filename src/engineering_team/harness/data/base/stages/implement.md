---
expected_output: >
  The work package implemented in the workspace with tests. Report the criteria delivered,
  files changed, commands run with their exit codes, and any honest limitation.
---
Implement one work package of {project_name} in {workspace_path}.

Specification:

{spec}

Plan:

{plan}

Your work package:

{package}

Read the architecture and the code that exists before editing. Implement real, end to end
behaviour for the acceptance criteria this package delivers, add focused tests next to the
code, validate inputs and error paths, keep secrets out of the repository, and run the
relevant tests or smoke checks. You may change only the paths your package owns
(owned_paths); teammates are working on the others at the same time, so everything else is
read-only for you. If you need a change elsewhere (a shared file, another package's
interface), do not work around it: describe it in your summary or in a card comment so the
integration stage can make it.

{handoff}
{card}
{notes}
{resume_note}
