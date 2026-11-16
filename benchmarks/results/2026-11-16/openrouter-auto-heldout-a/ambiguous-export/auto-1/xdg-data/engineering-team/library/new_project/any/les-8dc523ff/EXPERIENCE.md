---
id: les-8dc523ff
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-185653-945594
  verdict: verified
  check_ids:
  - tests
  revision: 384fa78558ea0f4d2df9cefcc962934c
  rows:
  - behaviour: Created the package layout, implementation, tests, README and .gitignore from an empty
      workspace, then ran the real test command and compile check locally before handing off (#7-#11).
    guidance: Read before editing and run real checks instead of assuming.
    effect: The first controller check still found no tests (verify/tests failed), but the agent's own
      unittest run (#12) and Run Tests call (#18) passed 3 tests, matching the controller's later passing
      result.
    verdict: helped
    evidence:
    - 'verify/tests: failed (No tests ran)'
    - 'verify/tests: passed (3 passed, 0 failed, 0 skipped)'
    - '#7-#12'
    - '#18'
  - behaviour: Used the dedicated Run Tests tool with the correct test path and filter after writing unittest-style
      tests (#18).
    guidance: Run real checks instead of assuming.
    effect: The tool reported 3 passed, 0 failed, 0 skipped, and the controller's later tests check passed
      with the same count.
    verdict: helped
    evidence:
    - 'verify/tests: passed (3 passed, 0 failed, 0 skipped)'
    - '#18'
  - behaviour: Test discovery did not pick up the tests on the first controller run (verify/tests failed
      with 'No tests ran').
    guidance: The generalist guidance said to run real checks but did not mention that the harness's test
      discovery expects a parti...
    effect: A repair round was required; after repair the controller's tests check passed (3 passed).
      The trajectory portion covering the repair is omitted, so the specific cause of the initial discovery
      failure cannot be attributed.
    verdict: confounded
    evidence:
    - 'verify/tests: failed (No tests ran)'
    - 'verify/tests: passed (3 passed, 0 failed, 0 skipped)'
    - 'Repair round 1: sent tests to the repair agent; all of them pass now'
created: '2026-11-16T19:02:00.280905Z'
review: auto
---
# Lesson les-8dc523ff (new_project/*)

## Cue

A Python project is created from an empty workspace with unittest-based tests that the harness's own test discovery initially reports as 'No tests ran'.

## Lesson

Add guidance to teammates/generalist_engineer/tool_guidance stating that tests must live where the harness's default discovery expects them (for example a top-level tests/ directory with filenames matching test_*.py) and that the agent should confirm the discovery command reports a non-zero collected test count before handing off, not just that the local test script exits 0.
