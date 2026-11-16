---
id: les-3f11be95
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-193401-c7b21a
  verdict: verified
  check_ids:
  - tests
  revision: 5f6d7b383bc894ec9ef24539be0cfa97
  rows:
  - behaviour: Added tests under `tests/` but did not initially make that directory a package for the
      root-level unittest discovery command used by verification.
    guidance: teammates/generalist_engineer/tool_guidance (run real checks)
    effect: The controller's initial discovery check failed because no tests ran; after adding `tests/__init__.py`,
      the same discovery check passed all five tests.
    verdict: hurt
    evidence:
    - tests
    - '#16'
    - '#17'
    - '#18'
created: '2026-11-16T19:35:09.636489Z'
review: auto
---
# Lesson les-3f11be95 (new_project/*)

## Cue

A Python project uses unittest discovery from the repository root, with tests stored in a subdirectory.

## Lesson

Change guidance to ensure the chosen unittest discovery command can discover tests from the project root, including making the test directory a package when required, before treating verification as complete.
