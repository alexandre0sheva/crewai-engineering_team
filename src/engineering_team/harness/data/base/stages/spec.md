---
expected_output: >
  A JSON specification (title, summary, criteria, non_goals, assumptions, open_questions,
  confidence, blocking_questions).
---
Turn the request for {project_name} into a testable specification. Do not write files
and do not design the solution: say what must be true, not how to build it.

Request:

{requirements}

{context}

Produce a specification with: a short title and summary; acceptance criteria that can each
be checked by running or inspecting the result, numbered AC-1, AC-2, ... (each id used once,
never renumbered) with a kind of functional, quality, constraint, or security; explicit
non-goals; and the assumptions you make where the request is silent. Keep it to the
smallest useful MVP and invent nothing the request does not support.

Also report your confidence in your reading of the request (high, medium, or low). List a
blocking question only when its answer would materially change the product and you cannot
reasonably assume it; for each one, also write the answer you would assume under
assumptions, so work can go on if nobody answers. Put minor uncertainties in
open_questions, not in blocking_questions. Ask at most five blocking questions.

{handoff}
{card}
{notes}
{resume_note}
