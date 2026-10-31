---
expected_output: >
  A JSON specification (title, summary, criteria, non_goals, assumptions, open_questions,
  confidence, blocking_questions).
---
Turn the request into a testable specification for a CHANGE to an existing project,
{project_name}, in {workspace_path}. Do not write files and do not design the solution.

Request:

{requirements}

{context}

Produce a specification with: a short title and summary; acceptance criteria numbered
AC-1, AC-2, ... (each id used once) that can each be checked by running or inspecting the
result, with a kind of functional, quality, constraint, or security; explicit non-goals;
and the assumptions you make where the request is silent. Describe the new behaviour only
as it differs from what the project does today (read the project first if the request is
ambiguous), and add a criterion for each existing behaviour the change could plausibly break
and must keep (kind constraint), so that regressions are named. Keep it to the smallest
change that satisfies the request and invent nothing the request does not support.

Also report your confidence (high, medium, or low). List a blocking question only when its
answer would materially change the change and you cannot reasonably assume it; for each,
also write the answer you would assume under assumptions. Minor uncertainties go in
open_questions. Ask at most five blocking questions.

{handoff}
{card}
{notes}
{resume_note}
