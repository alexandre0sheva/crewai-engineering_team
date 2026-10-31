---
expected_output: >
  The refactor in the workspace with the project's tests passing, and a short summary: what was
  changed and why behaviour is unchanged, files touched, commands run with exit codes, and what
  was left alone.
---
Refactor the existing project {project_name} in {workspace_path} WITHOUT changing its
behaviour. The project's tests pass now; they must pass unchanged afterwards.

What to refactor (the person's goal):

{requirements}

{context}

Read the code and its tests first (the symbol, reference and import tools show who depends on
what). Make the smallest set of behaviour-preserving changes that achieves the goal (extract,
rename, simplify, remove duplication) and follow the conventions around the code. Hard rules,
checked by the controller: never edit, delete or skip an existing test; never touch a
dependency manifest or lockfile; keep the diff within maintain.max_refactor_lines (split a
bigger refactor and do the first part). Do not change public interfaces, outputs, error
messages, or file formats. Run the project's tests after each step and stop at a state where
they pass. If the goal cannot be reached without changing behaviour, do the safe part and say
what is left and why.

{handoff}
{card}
{notes}
{resume_note}
