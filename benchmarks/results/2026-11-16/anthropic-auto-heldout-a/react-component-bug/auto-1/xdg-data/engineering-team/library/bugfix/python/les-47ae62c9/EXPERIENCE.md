---
id: les-47ae62c9
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-195138-64db97
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: fc1c603e46b53685988ffa579fe4f35a
  rows:
  - behaviour: 'Wrote a regression test that reproduces the frozen-array crash before fixing (#2, #3),
      rather than only adding a test after the fix; the test failed on the original code as the request
      expected.'
    guidance: ''
    effect: 'Baseline repro failed (exit 1) and the targeted test went from failing (#8) to passing (#13)
      after the fix in #12; full npm test passed (#15).'
    verdict: helped
    evidence:
    - '#2'
    - '#3'
    - '#8'
    - '#12'
    - '#13'
    - '#15'
    - repro
  - behaviour: Fixed OrderSummary by sorting a copy instead of the caller's array, and added a CartPage
      test for render order, covering the second expectation in the request.
    guidance: ''
    effect: Targeted test and full npm test passed; verify tests and repro passed. The CartPage test was
      written (#14) and the suite passed with 4 tests across 2 files.
    verdict: helped
    evidence:
    - '#12'
    - '#14'
    - '#15'
    - tests
    - repro
created: '2026-11-16T19:52:55.943310Z'
review: auto
---
# Lesson les-47ae62c9 (bugfix/python)

## Cue

Bug report where a UI component sorts or mutates an array prop that a store has frozen, and the fix must leave the caller's order untouched

## Lesson

Add guidance to the fix workflow: when a component's input may be frozen or shared, reproduce with a test that freezes the input and asserts it is unchanged, then sort or transform a copy; verify the caller-order regression with a separate render-order test.
