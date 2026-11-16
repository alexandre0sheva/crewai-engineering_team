---
id: les-2221df43
domain: bugfix/python
component: teammates/debugger/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-220235-f833e9
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 844bc513553a86e7f95cf4807df35f59
  rows:
  - behaviour: Added a regression test for a Rabobank amount with an explicit plus sign, confirmed it
      failed, then changed the amount pattern to accept either sign.
    guidance: 'teammates/debugger/tool_guidance: reproduce the failure and fix its cause with the smallest
      change.'
    effect: The targeted test and full test suite passed after the parser change; the controller also
      verified the report reproduction passed.
    verdict: helped
    evidence:
    - repro
    - tests
    - '#8'
    - '#11'
    - '#20'
    - '#19'
created: '2026-11-16T22:04:01.487147Z'
review: auto
---
# Lesson les-2221df43 (bugfix/python)

## Cue

A bank import accepts unsigned and negative amounts, but a valid explicitly positive amount is silently parsed as missing.

## Lesson

Add guidance to test signed positive amounts as well as negative and unsigned forms when changing amount parsing, and verify the import and downstream report preserve the value.
