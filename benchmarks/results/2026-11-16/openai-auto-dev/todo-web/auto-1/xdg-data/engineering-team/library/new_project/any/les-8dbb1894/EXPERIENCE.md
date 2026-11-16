---
id: les-8dbb1894
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-211802-2ffb3e
  verdict: verified
  check_ids:
  - tests
  revision: 65c961087b602cb18c53a59345ebf831
  rows:
  - behaviour: The agent wrote the implementation and tests, then ran the initial checks, which failed;
      it made a targeted replacement and reran both checks successfully.
    guidance: 'teammates/generalist_engineer/tool_guidance: run real checks instead of assuming.'
    effect: The controller's verify check passed all 3 tests; compilation also succeeded on the later
      run.
    verdict: helped
    evidence:
    - '#5'
    - '#6'
    - '#9'
    - '#10'
    - '#11'
    - tests
created: '2026-11-16T21:19:27.063232Z'
review: auto
---
# Lesson les-8dbb1894 (new_project/*)

## Cue

A small project where an initial implementation fails compilation or tests and a focused edit may resolve it.

## Lesson

Keep the instruction to run real checks after edits, and make follow-up edits only after identifying a concrete issue in the failed check output.
