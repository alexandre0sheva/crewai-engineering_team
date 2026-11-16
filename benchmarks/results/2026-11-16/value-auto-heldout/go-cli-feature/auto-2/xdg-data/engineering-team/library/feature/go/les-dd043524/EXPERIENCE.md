---
id: les-dd043524
domain: feature/go
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-213658-dc8636
  verdict: verified
  check_ids:
  - tests
  - typecheck
  - build
  revision: 015cc50a5ae511d7085157f8b915ed7a
  rows:
  - behaviour: After an initial failing test run, the agent inspected the implementation, made another
      change, added tests for the new flags, and reran tests and build successfully.
    guidance: 'teammates/generalist_engineer/tool_guidance: run real checks instead of assuming.'
    effect: 'The controller''s verify checks passed: tests, typecheck, and build.'
    verdict: helped
    evidence:
    - '#11'
    - '#12'
    - '#13'
    - '#14'
    - '#15'
    - '#16'
    - tests
    - typecheck
created: '2026-11-16T21:39:00.352386Z'
review: auto
---
# Lesson les-dd043524 (feature/go)

## Cue

A feature change has an initial failing test run, and the agent needs to diagnose and validate the repair.

## Lesson

Keep the instruction to rerun the relevant tests after a repair and run a build before considering the change complete.
