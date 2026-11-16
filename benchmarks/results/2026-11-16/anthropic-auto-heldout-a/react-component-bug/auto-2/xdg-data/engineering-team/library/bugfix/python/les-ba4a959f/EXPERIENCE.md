---
id: les-ba4a959f
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-195251-ff3b2f
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 87ce29f124ac8a8756d279abbb1973f6
  rows:
  - behaviour: Agent wrote a regression test in tests/OrderSummary.test.tsx first and ran it to reproduce
      the failure before editing source; the repro then failed with the expected read-only error and was
      used as the lead's target.
    guidance: ''
    effect: 'Reproduce stage ran a failing test (expected_failure recorded) before the fix; after the
      lead''s edit, the OrderSummary test and full npm test both passed (#18, #20). Check repro/tests
      in verify passed.'
    verdict: helped
    evidence:
    - '#2'
    - '#11'
    - '#12'
    - '#14'
    - '#18'
    - '#20'
  - behaviour: Agent spent several calls on environment probing (vitest.mjs find, Run Tests tool returning
      UNAVAILABLE, out-of-workspace path, npm ls, failed Install Dependencies) before running npm install
      and npx vitest directly.
    guidance: ''
    effect: Baseline tests check failed (0 tests run) and about 7 calls were consumed before the test
      runner worked; no check result was affected after npm install succeeded.
    verdict: no observed effect
    evidence:
    - '#3'
    - '#4'
    - '#5'
    - '#7'
    - '#8'
    - '#9'
    - '#10'
    - baseline / tests
created: '2026-11-16T19:54:31.184644Z'
review: auto
---
# Lesson les-ba4a959f (bugfix/python)

## Cue

Bug where a component sorts or mutates an input array or its objects in place, and state is frozen or shared so the mutation throws or reorders caller data.

## Lesson

Add guidance to teammates/generalist_engineer/tool_guidance: when a fix touches code that sorts or mutates props, copy the array before sorting (for example [...lines].sort) and add a test that freezes the input and checks caller order is unchanged; verify the test fails before the edit.
