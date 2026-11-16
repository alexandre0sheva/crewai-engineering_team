---
id: les-692de768
domain: feature/go
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-193532-18caba
  verdict: verified
  check_ids:
  - tests
  - build
  revision: 924ea9ac4afdbdb80dd290692002ea6b
  rows:
  - behaviour: Ran tests before completing the implementation, observed a failure, then edited the counting
      package and reran tests and build successfully.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The intermediate test run failed; after the repair, the recorded tests and build passed.
    verdict: hurt
    evidence:
    - '#13'
    - '#14'
    - '#15'
    - '#16'
    - tests
    - build
created: '2026-11-16T19:36:48.504109Z'
review: auto
---
# Lesson les-692de768 (feature/go)

## Cue

A small Go feature changes behavior spanning command-line handling and a core package, and an early test run fails before implementation is complete.

## Lesson

Change guidance to prefer completing the planned implementation before treating an early failing test run as a signal for a repair; then run formatting through an allowed Go command and rerun the full checks.
