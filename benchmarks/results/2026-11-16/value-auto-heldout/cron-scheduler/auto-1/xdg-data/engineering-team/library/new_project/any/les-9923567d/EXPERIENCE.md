---
id: les-9923567d
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-213408-c1df12
  verdict: verified
  check_ids:
  - tests
  revision: 141136c9a4e81df29e0f72967399e596
  rows:
  - behaviour: Created the tests directory without making it a package, so the verifier's configured discovery
      command initially found no tests; then added tests/__init__.py and reran discovery.
    guidance: 'teammates/generalist_engineer/tool_guidance: read before editing and run real checks instead
      of assuming.'
    effect: The controller recorded an initial tests check with no tests ran, followed by a repair round;
      after the package marker was added, all six tests passed.
    verdict: hurt
    evidence:
    - tests [test]
    - verify/tests failed
    - verify/tests passed
    - '#4'
    - '#16'
    - '#17'
    - '#18'
created: '2026-11-16T21:35:19.528003Z'
review: auto
---
# Lesson les-9923567d (new_project/*)

## Cue

A Python project uses unittest discovery with a repository-root top-level directory and tests reside in a subdirectory.

## Lesson

Change the test-discovery guidance to require ensuring the tests directory is importable (for example, by adding tests/__init__.py) when using root-based discovery, and verify that the configured command actually discovers tests.
