---
id: les-fcb79f3e
domain: feature/python
component: stages/acceptance
confidence: medium
evidence:
  run_id: 20261116-215054-dd785f
  verdict: failed
  check_ids:
  - acceptance-1
  - tests
  - acceptance-2
  revision: 2b1ca06587952372c2e75f00c3521384
  rows:
  - behaviour: The acceptance engineer added tests whose ordering assertions conflict with the requested
      ordering (including expecting insertion order or a higher id first where the specification requires
      quantity-ascending and id tie-break order).
    guidance: 'teammates/quality_engineer/tool_guidance: test behavior against the specification and cover
      boundary cases.'
    effect: The acceptance checks failed, and the three corresponding tests remained among the verify
      failures. Repair agents reproduced the failures but made no project changes; the final run still
      failed.
    verdict: hurt
    evidence:
    - acceptance-1
    - tests
    - acceptance-2
    - '#12'
    - '#14'
    - '#18'
    - '#66'
    - '#70'
created: '2026-11-16T21:55:28.557509Z'
review: auto
---
# Lesson les-fcb79f3e (feature/python)

## Cue

When acceptance tests for a requested feature fail on behavior that appears to contradict the written specification, especially ordering or tie-break rules.

## Lesson

Change the acceptance stage guidance to cross-check each assertion against the request's explicit behavior before adding tests, and resolve apparent conflicts in favor of the request rather than encoding a contradictory expectation.
