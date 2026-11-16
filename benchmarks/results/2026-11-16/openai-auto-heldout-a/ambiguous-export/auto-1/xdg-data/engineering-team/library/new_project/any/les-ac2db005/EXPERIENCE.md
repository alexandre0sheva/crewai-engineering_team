---
id: les-ac2db005
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-193251-688e6d
  verdict: verified
  check_ids:
  - tests
  revision: efa7d2de699cbb64f632e003d7f5b01b
  rows:
  - behaviour: Added an empty tests package marker after the initial unittest run errored, then reran
      the tests.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The subsequent controller test check passed all three tests; the marker change is consistent
      with fixing test discovery, though the controller record does not expose the initial error details.
    verdict: helped
    evidence:
    - '#7'
    - '#8'
    - '#10'
    - tests
created: '2026-11-16T19:33:52.330428Z'
review: auto
---
# Lesson les-ac2db005 (new_project/*)

## Cue

A new Python project uses unittest discovery from a tests directory and the first discovery run errors before executing tests.

## Lesson

Add guidance to ensure the test directory is discoverable (for example, by adding its package marker when required by the discovery setup), then rerun the checks.
