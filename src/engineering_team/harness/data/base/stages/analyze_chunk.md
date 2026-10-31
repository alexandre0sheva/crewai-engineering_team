---
expected_output: >
  A JSON analysis (summary, modules, key_flows, conventions, risks, tests).
---
Analyse one part of the existing codebase of {project_name} in {workspace_path}. You can
only read it: do not write or change any file, and do not describe anything you have not
seen in the code.

{chunk}

Read this chunk's entry points, modules, and tests (the symbol, reference, import, and
search tools help you find your way; Hotspots shows where the history churns). Report:
a short summary of what this part is for; its main modules (name, path, one-line purpose,
and which other modules each uses); the key flows through it, as short sentences that
name real functions or files; the conventions the code actually follows (naming, error
handling, structure, test style) so a later change can match them; the risks you can
point to; and how this part is tested and what is not. Keep every item to one sentence.
Say "unclear" instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
