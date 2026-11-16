---
id: les-e9cb8468
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-195532-8d8bf5
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 9a81faa1332b114a7aff2a3b9ee907d1
  rows:
  - behaviour: Added a focused regression test for a positively signed Rabobank salary amount, then fixed
      the parser to accept the explicit plus sign and reject unreadable non-balance amounts with a file-and-line
      error while retaining the blank opening-balance behavior.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The focused regression test passed after the change; the full suite passed with 37 tests,
      and the controller's March repro passed.
    verdict: helped
    evidence:
    - '#7'
    - '#8'
    - '#17'
    - '#19'
    - '#20'
    - tests
    - repro
created: '2026-11-16T19:56:38.856424Z'
review: auto
---
# Lesson les-e9cb8468 (bugfix/python)

## Cue

A bug report involves bank-statement parsing where signed locale-formatted amounts can be silently lost or malformed lines risk creating incomplete transactions.

## Lesson

Add guidance to require a regression test for the problematic amount form and to fail atomically with a file-and-line error for unreadable transaction lines, while explicitly preserving legitimate non-transaction balance-line handling.
