---
id: les-3041b34b
domain: bugfix/typescript
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-210257-bdb4f6
  verdict: verified
  check_ids:
  - repro
  - tests
  revision: 084a19b5c25d0530980f131c5e580b8c
  rows:
  - behaviour: Added a regression test that reproduced the reported `59m60s` output before changing the
      formatter, then changed the formatter to round the total once before splitting it into units.
    guidance: teammates/generalist_engineer/tool_guidance (read before editing and run real checks)
    effect: The initial regression test failed, and after the fix the regression test and full suite passed;
      the controller's repro check also passed. The test checks report 0 passed, so they provide no test-count
      evidence beyond their recorded pass status.
    verdict: helped
    evidence:
    - '#4'
    - '#5'
    - '#12'
    - '#14'
    - '#15'
    - repro
created: '2026-11-16T21:13:53.191232Z'
review: auto
---
# Lesson les-3041b34b (bugfix/typescript)

## Cue

A duration formatter rounds values while decomposing them into multiple units, so rounding a lower unit can produce a value equal to that unit's base.

## Lesson

Change guidance to round the total duration once before splitting it into units, and verify boundary cases with regression tests and the project checks.
