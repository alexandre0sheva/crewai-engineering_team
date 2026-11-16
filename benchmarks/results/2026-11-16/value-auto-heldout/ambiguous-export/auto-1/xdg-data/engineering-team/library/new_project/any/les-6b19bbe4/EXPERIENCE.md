---
id: les-6b19bbe4
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-203821-0fe0c7
  verdict: verified
  check_ids:
  - tests
  revision: 1cd5ef2ba5b8cf97f35251b334ce3c8d
  rows:
  - behaviour: Read the project tree before editing, then built the requested CLI, tests, and documentation
      from scratch.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The controller's tests passed (3 passed, 0 failed, 0 skipped).
    verdict: helped
    evidence:
    - '#1'
    - '#2'
    - '#3'
    - '#4'
    - '#5'
    - tests
  - behaviour: Ran tests and exercised the CLI help command; after changing argument-parser setup, ran
      the help command again.
    guidance: teammates/generalist_engineer/tool_guidance
    effect: The controller later confirmed the tests passed. The trajectory shows successful help invocations,
      but does not establish that the parser change fixed a failure.
    verdict: no observed effect
    evidence:
    - '#6'
    - '#7'
    - '#8'
    - '#9'
    - tests
created: '2026-11-16T20:39:46.191172Z'
review: auto
---
# Lesson les-6b19bbe4 (new_project/*)

## Cue

A small, self-contained Python CLI request in an empty project, with explicit input-error behavior and a requirement to include tests.

## Lesson

Keep the existing guidance to read before editing and run real checks; this run shows the requested CLI and tests can be implemented and verified directly without additional harness guidance.
