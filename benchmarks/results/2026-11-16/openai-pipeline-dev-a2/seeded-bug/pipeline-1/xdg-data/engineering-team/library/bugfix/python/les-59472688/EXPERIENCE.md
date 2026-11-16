---
id: les-59472688
domain: bugfix/python
component: teammates/debugger/tool_guidance
confidence: low
evidence:
  run_id: 20261116-215912-b50904
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: ae72bbe4b9768b8952412b4f8919d333
  rows:
  - behaviour: Added a regression test for reporting an empty cart before changing the implementation;
      the first patch attempt failed to match, then the test was added and reproduced the ZeroDivisionError.
    guidance: 'teammates/debugger/tool_guidance: reproduce the failure before changing anything.'
    effect: The controller's reproduce check failed, and the final verification passed the regression
      suite and repro after the fix.
    verdict: helped
    evidence:
    - repro (failed)
    - '#8'
    - '#9'
    - tests (passed)
    - repro (passed)
  - behaviour: The fix addressed both the empty-cart average and report formatting, rather than changing
      only the division; the review found no defects.
    guidance: 'teammates/debugger/tool_guidance: fix the cause with the smallest change that does.'
    effect: The controller verified all four tests and the repro passed; the reviewer reported no findings.
    verdict: helped
    evidence:
    - tests (passed)
    - repro (passed)
    - '#16'
    - '#27'
created: '2026-11-16T22:00:24.511497Z'
review: auto
---
# Lesson les-59472688 (bugfix/python)

## Cue

A bug request requires a new edge-case behavior and specifies both an API result and user-facing output.

## Lesson

Change guidance to add a regression test that checks the specified end-to-end output as well as the underlying edge-case behavior before implementing the fix.
