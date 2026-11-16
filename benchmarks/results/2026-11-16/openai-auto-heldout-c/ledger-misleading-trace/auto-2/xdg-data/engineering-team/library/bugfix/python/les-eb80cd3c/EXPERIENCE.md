---
id: les-eb80cd3c
domain: bugfix/python
component: stages/lead
confidence: medium
evidence:
  run_id: 20261116-195532-b2457a
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 39c964a5892bc2502482ae4934fd239e
  rows:
  - behaviour: Added a regression test for a positive-signed Rabobank salary and March income report;
      it reproduced a TypeError before the fix.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The failing reproduction identified the report crash, and after the import fix the regression
      test passed with the full suite.
    verdict: helped
    evidence:
    - repro
    - tests
    - '#3'
    - '#4'
    - '#15'
  - behaviour: Changed Rabobank parsing to accept an explicit `+` on decimal-comma amounts and to raise
      a file-and-line import error for unreadable non-balance amounts.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: Controller records show the regression and full suite passed, and the reproduction passed
      after the fix.
    verdict: helped
    evidence:
    - tests
    - repro
    - '#12'
    - '#15'
created: '2026-11-16T19:56:29.042469Z'
review: auto
---
# Lesson les-eb80cd3c (bugfix/python)

## Cue

A bank importer accepts signed, locale-formatted amounts, and a valid positive amount is omitted or causes a downstream report failure.

## Lesson

Add a regression test covering explicitly positive amounts, then change the parser guidance to preserve their value and reject unreadable transaction amounts with source location rather than silently omitting them.
