---
expected_output: >
  A JSON plan (stack, commands left empty, work_packages with owned_paths and criteria_ids,
  risks).
---
Plan the change to the existing project {project_name} in {workspace_path}. Do not edit
any file: you design, the others implement.

Specification:

{spec}

Original request:

{requirements}

{context}

First read the code the change touches (the codebase map above is a guide, not evidence:
confirm it in the code; the symbol, reference, import, and test-finding tools show who
depends on what). Then produce the impact plan as JSON: the project's existing stack
(do not change it); leave commands empty (the controller uses the project's own commands);
work packages, each with an id, a title, a role (a teammate from the list below), a
description that names the files to change or add, the migrations or data changes it needs,
and how it is tested, the exact owned_paths (the files or directories it may change: as
narrow as possible, never a directory that holds unrelated code; a root file such as
package.json or pyproject.toml is allowed when the change needs it), depends_on, and the
criteria_ids it delivers. Packages that do not depend on each other and own different paths
run at the same time. Also list the risks: what the change could break, what is untested,
what needs a human decision. Follow the conventions the code already has; plan the smallest
change that delivers the criteria, and no refactoring the request did not ask for.

Teammates available for work packages (a package's role is a teammate's key, or its short
form without "_engineer"):

{teammates}

{handoff}
{card}
{notes}
{resume_note}
