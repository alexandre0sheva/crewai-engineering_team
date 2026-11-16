---
id: les-eee06720
domain: feature/python
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-210105-b929f5
  verdict: verified
  check_ids:
  - tests
  revision: 2cf7103ee0b796b7af93d1fb7293d61d
  rows:
  - behaviour: The agent ran the requested test suite after its first edit; when it failed, it revised
      the tests and reran them, obtaining a pass.
    guidance: teammates/generalist_engineer/tool_guidance (run real checks instead of assuming)
    effect: The final controller verification passed all six tests.
    verdict: helped
    evidence:
    - '#9'
    - '#11'
    - '#12'
    - tests
created: '2026-11-16T21:01:55.562759Z'
review: auto
---
# Lesson les-eee06720 (feature/python)

## Cue

A small Python feature adds a new HTTP endpoint and its tests to an existing service.

## Lesson

Keep the guidance to run the project test suite after implementation; if it fails, inspect the failure, make a targeted correction, and rerun the suite before reporting completion.
