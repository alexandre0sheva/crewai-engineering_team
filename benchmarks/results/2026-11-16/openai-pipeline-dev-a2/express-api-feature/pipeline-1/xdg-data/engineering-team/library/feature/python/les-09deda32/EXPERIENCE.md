---
id: les-09deda32
domain: feature/python
component: stages/acceptance
confidence: low
evidence:
  run_id: 20261116-215054-27cd2c
  verdict: failed
  check_ids:
  - acceptance-1
  revision: e8ed9d01ddef682c2dee366ae2fd8abb
  rows:
  - behaviour: The acceptance test suite was expanded with multiple files and shared test-server state,
      then the `AC-1` name-pattern check was used even though it also selects the `AC-10` test; the repair
      agent reproduced the issue but made no project changes across three repair rounds.
    guidance: teammates/quality_engineer/tool_guidance; teammates/debugger/tool_guidance
    effect: The controller's acceptance-1 check remained failing and the run ended with verification failed.
      The debugger identified that AC-1's extra book creation changes the sequential ID seen by AC-10;
      the narrower AC-10 command passed. The final suite check passed, but acceptance-1 did not.
    verdict: hurt
    evidence:
    - acceptance-1
    - '#13'
    - '#14'
    - '#25'
    - '#26'
    - '#78'
    - '#89'
    - '#94'
created: '2026-11-16T21:57:53.393218Z'
review: auto
---
# Lesson les-09deda32 (feature/python)

## Cue

A test command selects tests by substring or name pattern, and those tests share mutable fixtures or state that affects order-dependent assertions.

## Lesson

Change the acceptance stage to use exact test-name selection or isolated fixtures for checks that assert sequential state, so a targeted check cannot unintentionally run similarly named tests against shared state.
