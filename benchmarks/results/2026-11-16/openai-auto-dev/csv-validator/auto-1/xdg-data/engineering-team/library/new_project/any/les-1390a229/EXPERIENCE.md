---
id: les-1390a229
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-205608-c29d4f
  verdict: verified
  check_ids:
  - tests
  revision: 94112c017fe332214f91d0aefb7b26a2
  rows:
  - behaviour: Initially ran unittest discovery with `-s tests`, then changed a test expectation after
      it failed; later the verify stage ran discovery with `-t .`, which found no tests until `tests/__init__.py`
      was added.
    guidance: 'teammates/generalist_engineer/tool_guidance: run real checks instead of assuming; the harness
      guidance did not specif...'
    effect: The controller's first verify test check found no tests; after the repair round, the rerun
      passed with 4 tests.
    verdict: hurt
    evidence:
    - tests [test]
    - '#6'
    - '#7'
    - '#18'
    - '#19'
    - '#20'
  - behaviour: The engineer followed the read-before-edit guidance in verify by inspecting the tests,
      implementation, and README before addressing the discovery failure.
    guidance: 'teammates/generalist_engineer/tool_guidance: read before editing.'
    effect: The recorded repair made the verify discovery command find and pass all four tests.
    verdict: helped
    evidence:
    - tests [test]
    - '#15'
    - '#16'
    - '#17'
    - '#18'
    - '#19'
    - '#20'
created: '2026-11-16T20:57:45.358674Z'
review: auto
---
# Lesson les-1390a229 (new_project/*)

## Cue

A Python project has tests that pass under one unittest discovery invocation, but a later stage may use a different discovery command from the project root.

## Lesson

Change the test-discovery guidance to require checking that tests are discoverable with the harness's exact command, and add the minimal package/discovery setup if they are not.
