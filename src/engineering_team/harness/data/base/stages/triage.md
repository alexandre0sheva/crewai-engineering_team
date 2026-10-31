---
expected_output: >
  A JSON triage (title, summary, hypotheses with suspects, evidence and likelihood,
  suspect_files, reproduction_idea).
---
Triage a bug in the existing project {project_name} in {workspace_path}. Do not change any
file in this step: you find where to look, the next step reproduces it.

The bug report:

{requirements}

{bug}

{context}

Read the report, then the code it points at (the codebase map above is a guide, not
evidence: confirm it in the code; the search, symbol, reference, import, and Git history
tools show who calls what and what changed recently). If there is a stack trace, start at its
innermost project frame. Return a short title for the bug, a summary of what goes wrong and
what should happen instead, and two or three ranked hypotheses, most likely first: each says
what you think is wrong, which project files (relative paths) are the suspects, what in the
code, the trace, or the history is the evidence, and how likely it is (high, medium, low).
Also say how a failing automated test or a small script could show the bug (reproduction_idea):
the input, the call, the wrong result, and what the right one is. Name only files you have
seen, and say "unclear" instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
