---
expected_output: >
  A JSON object with "spec" (title, summary, criteria, non_goals, assumptions,
  open_questions, confidence, blocking_questions) and "plan" (stack, commands, work_packages,
  risks).
---
Specify and plan {project_name}, a new project in {workspace_path}, in one pass. Do not
write files: the controller records your answer in docs/spec.md and docs/plan.md.

Request:

{requirements}

{context}

First the specification: a short title and summary; acceptance criteria that can each be
checked by running or inspecting the result, numbered AC-1, AC-2, ... (each id used once)
with a kind of functional, quality, constraint, or security; explicit non-goals; the
assumptions you make where the request is silent; your confidence (high, medium, or low);
and a blocking question only when its answer would materially change the product and you
cannot reasonably assume it (write the answer you assume under assumptions; at most five).
Keep it to the smallest useful MVP and invent nothing the request does not support.

Then the plan for that specification: the smallest suitable stack (do not force Python, a
web UI, or any framework the request does not need); the exact project commands (setup,
test, lint, build, run) as lists of command lines; and work packages, each with an id
(WP-1, ...), a title, a role (a teammate's key below, or its short form without
"_engineer"), a description, the paths it owns (globs such as "src/api/**"), the ids of the
packages it depends on, and the criteria ids it delivers (every criterion delivered by some
package). Use as few packages as the work allows: one is right for a small project.
Packages that do not depend on each other run at the same time, so give them disjoint
paths, and never give one a shared root file (README.md, package.json, pyproject.toml, a
lockfile, CI configuration). Also list the main risks.

Teammates available for work packages:

{teammates}

{handoff}
{card}
{notes}
{resume_note}
