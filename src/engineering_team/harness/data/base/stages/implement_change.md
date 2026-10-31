---
expected_output: >
  The work package implemented with the smallest diff. Report the criteria delivered, files
  changed, commands run with their exit codes, and any honest limitation.
---
Implement one work package of a change to the existing project {project_name} in
{workspace_path}.

Specification:

{spec}

Plan:

{plan}

Your work package:

{package}

{context}

Read the code you will change and the code around it first, and follow what you find: its
naming, structure, error handling, comments, and test style, and the project's lint and
format configuration. Keep the diff minimal: change only what the criteria need, do not
reformat, rename, reorder, or tidy code you were not asked to change, and do not touch
generated files or lockfiles by hand. Run the project's own tests and checks for what you
changed. You may change only the paths your package owns (owned_paths); teammates work on
the others at the same time, so everything else is read-only for you. If you need a change
elsewhere, describe it in your summary or in a card comment instead of working around it.

{handoff}
{card}
{notes}
{resume_note}
