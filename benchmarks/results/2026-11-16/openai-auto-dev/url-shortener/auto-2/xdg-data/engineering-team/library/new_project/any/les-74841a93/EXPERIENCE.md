---
id: les-74841a93
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-212011-e9542e
  verdict: verified
  check_ids:
  - tests
  revision: 904946a2918b938539f21c42edb03a8b
  rows:
  - behaviour: Ran the test suite before adding tests/__init__.py; the initial run errored, and the subsequent
      run still had an error, prompting further test edits.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The controller's final tests passed after additional test changes; the earlier errors caused
      repair iterations.
    verdict: hurt
    evidence:
    - '#6'
    - '#7'
    - '#9'
    - '#10'
    - '#11'
    - '#12'
    - '#13'
    - tests
created: '2026-11-16T21:21:50.698678Z'
review: auto
---
# Lesson les-74841a93 (new_project/*)

## Cue

A small Python project has just been created and its unittest suite is being run for the first time.

## Lesson

Change the test-running workflow to check that the test directory is discoverable by unittest (for example, make it a package) before relying on the initial run.
