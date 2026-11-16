---
id: les-f75c76d6
domain: bugfix/python
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-213758-af9c59
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: ac4208059c676300b32ae50a521f69b4
  rows:
  - behaviour: Changed OrderSummary to sort a copy of the lines rather than the caller's array, and added
      CartPage/order regression tests.
    guidance: Request requirement; no harness guidance specifically covered preserving input immutability
      or adding these regressio...
    effect: The controller's verification checks passed; the agent also reported the full suite passed
      after the change.
    verdict: helped
    evidence:
    - tests
    - repro
    - '#34'
    - '#35'
    - '#36'
    - '#39'
created: '2026-11-16T21:40:14.299974Z'
review: auto
---
# Lesson les-f75c76d6 (bugfix/python)

## Cue

A UI component receives arrays from immutable or shared application state and needs to present them in a different order.

## Lesson

Change guidance to require sorting a copy rather than the supplied array, and to add a regression test that checks both frozen inputs and preservation of caller order.
