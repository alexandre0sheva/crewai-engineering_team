---
id: les-a7996a86
domain: bugfix/typescript
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-211402-a09825
  verdict: verified
  check_ids:
  - repro
  - tests
  revision: 65fc48f93912aaf14ba82e367cb66b5b
  rows:
  - behaviour: Changed formatting to round the full duration before splitting it into units.
    guidance: 'teammates/generalist_engineer/tool_guidance: design simply and run real checks.'
    effect: The controller's repro check passed after the change, consistent with fixing the reported
      carry case. The tests check reported 0 tests, so its result does not verify the regression test.
    verdict: helped
    evidence:
    - repro
    - tests
    - '#15'
    - '#16'
    - '#17'
created: '2026-11-16T21:15:06.166283Z'
review: auto
---
# Lesson les-a7996a86 (bugfix/typescript)

## Cue

A formatter rounds a numeric total and then decomposes it into units, where rounding can make a component equal the next unit's base.

## Lesson

Change the formatting guidance to round the total once before splitting it into units, and check carry boundaries with a regression test.
