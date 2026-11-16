---
id: les-9e52cde0
domain: new_project/*
component: teammates/debugger/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-203409-ed3245
  verdict: verified
  check_ids:
  - tests
  revision: 9b6a4ce52e20c9fd0cd8a0cc7e6ccda6
  rows:
  - behaviour: The debugger ran unittest discovery with a top-level start directory, discovered it collected
      no tests, then added a tests package marker and reran discovery.
    guidance: teammates/debugger/tool_guidance and teammates/debugger/guardrails
    effect: The initial discovery command exited 5; after the repair, discovery passed with 5 tests, and
      the controller's later test check passed with 6.
    verdict: helped
    evidence:
    - '#22'
    - '#23'
    - '#24'
    - tests
created: '2026-11-16T20:37:13.257170Z'
review: auto
---
# Lesson les-9e52cde0 (new_project/*)

## Cue

A Python project uses unittest discovery with a top-level start directory, and discovery reports that no tests ran despite test files being present.

## Lesson

Change guidance to verify that the chosen discovery command actually collects and runs the intended tests; if it collects none, adjust the test package layout or discovery options and rerun.
