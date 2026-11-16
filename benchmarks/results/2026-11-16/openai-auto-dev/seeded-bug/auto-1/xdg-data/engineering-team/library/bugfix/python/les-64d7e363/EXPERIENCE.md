---
id: les-64d7e363
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-211646-ac84b0
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 7bb26932b2c232aee2ea5f87dc515058
  rows:
  - behaviour: Added a regression test for reporting an empty cart, then changed `Cart.average_price()`
      to return `None` when empty and updated report formatting to show `n/a`.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The controller recorded the reproduction as failing before the fix, then the full test suite
      passed (4 tests) and the reproduction passed after the fix.
    verdict: helped
    evidence:
    - 'repro (reproduce: failed)'
    - '#6'
    - '#7'
    - '#8'
    - '#18'
    - tests
    - 'repro (verify: passed)'
created: '2026-11-16T21:17:51.654211Z'
review: auto
---
# Lesson les-64d7e363 (bugfix/python)

## Cue

A bugfix request specifies a changed return value and corresponding CLI output, while requiring existing output to remain unchanged.

## Lesson

Add guidance to verify both the specified API behavior and its caller's output, with regression coverage for the empty case and a preservation check for non-empty cases.
