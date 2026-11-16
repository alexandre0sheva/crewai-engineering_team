---
id: les-49c046eb
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-193646-452da5
  verdict: verified
  check_ids:
  - repro
  - tests
  revision: 2f52fd3a7686d11c62c27fdad22cc96e
  rows:
  - behaviour: Changed OrderSummary to sort a shallow copy of lines when sortByName is enabled, rather
      than sorting the supplied array.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The controller's reproduce check passed after the change; the recorded test check also passed,
      though it reports zero tests.
    verdict: helped
    evidence:
    - repro
    - tests
    - '#13'
created: '2026-11-16T19:37:53.490146Z'
review: auto
---
# Lesson les-49c046eb (bugfix/python)

## Cue

A UI component sorts caller-provided data that may be immutable, and rendering must preserve the caller's ordering.

## Lesson

Change the implementation guidance to explicitly prefer sorting a shallow copy over mutating caller-provided arrays, and verify frozen-input and order-preservation behavior with regression tests.
