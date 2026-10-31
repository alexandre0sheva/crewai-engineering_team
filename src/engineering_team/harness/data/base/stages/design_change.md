---
expected_output: >
  A JSON object with "spec" (title, summary, criteria, non_goals, assumptions,
  open_questions, confidence, blocking_questions) and "plan" (stack, commands left empty,
  work_packages with owned_paths and criteria_ids, risks).
---
Specify and plan a CHANGE to the existing project {project_name} in {workspace_path}, in
one pass. Do not edit any file: you design, the others implement.

Request:

{requirements}

{context}

Read the code the change touches first (the codebase map, when there is one, is a guide,
not evidence; the search, symbol, reference, import, and test-finding tools show who
depends on what).

The specification: a short title and summary; acceptance criteria numbered AC-1, AC-2, ...
(each id used once) that can each be checked by running or inspecting the result, with a
kind of functional, quality, constraint, or security; a criterion (kind constraint) for
each existing behaviour the change could plausibly break and must keep; explicit
non-goals; the assumptions you make where the request is silent; your confidence (high,
medium, or low); and a blocking question only when its answer would materially change the
change and you cannot reasonably assume it (write the answer you assume under assumptions;
at most five). Describe the new behaviour only as it differs from what the project does
today, and invent nothing the request does not support.

The impact plan: the project's existing stack (do not change it); leave commands empty (the
controller uses the project's own commands); work packages, each with an id, a title, a
role (a teammate below), a description that names the files to change or add and how it
is tested, the exact owned_paths (as narrow as possible; a root file such as package.json
is allowed when the change needs it), depends_on, and the criteria_ids it delivers. Use as
few packages as the change allows: one is right for a small change. Also list the risks:
what the change could break, what is untested, what needs a human decision. Follow the
code's conventions and plan the smallest change that delivers the criteria.

Teammates available for work packages:

{teammates}

{handoff}
{card}
{notes}
{resume_note}
