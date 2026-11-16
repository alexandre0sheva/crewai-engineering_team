---
id: les-70725099
domain: docs/python
component: teammates/technical_writer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-192241-e2ddc8
  verdict: verified
  check_ids:
  - tests
  - policy:docs_only
  revision: 19ab67d858e0482ed701e22ae94667ec
  rows:
  - behaviour: The technical writer edited documentation files only, adding the requested ANSWERS.md and
      updating existing documentation examples rather than changing application code.
    guidance: teammates/technical_writer/guardrails
    effect: The docs-only policy check passed and the run's final verdict was verified.
    verdict: helped
    evidence:
    - 'trajectory #190'
    - 'trajectory #189'
    - 'trajectory #202'
    - 'check policy:docs_only: passed'
  - behaviour: The technical writer verified the documentation claim about the free-shipping threshold
      against the code before revising the wording, correcting the statement that a 45.00 order is free
      at a 50.00 threshold.
    guidance: teammates/technical_writer/tool_guidance
    effect: The finding was accepted by review as F-1 and fixed in round 1; subsequent verification and
      policy checks passed.
    verdict: helped
    evidence:
    - 'trajectory #207'
    - 'stage verify: succeeded'
    - 'repair rounds: Round 1'
    - 'check tests: passed'
    - 'check policy:docs_only: passed'
created: '2026-11-16T19:39:39.603549Z'
review: auto
---
# Lesson les-70725099 (docs/python)

## Cue

When documenting or verifying behavior, an agent needs to run project commands or targeted checks, but the available tool surface may not expose command execution.

## Lesson

Add explicit guidance for teammates to use an available execution/check tool when one exists, and to state plainly when command execution is unavailable instead of repeatedly querying for absent tools; also clarify that verification performed by the controller counts as check evidence but must be described accurately as controller-run rather than teammate-run.
