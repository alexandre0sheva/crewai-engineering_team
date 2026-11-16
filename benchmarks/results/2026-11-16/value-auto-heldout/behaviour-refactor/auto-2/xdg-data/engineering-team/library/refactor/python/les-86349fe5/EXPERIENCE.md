---
id: les-86349fe5
domain: refactor/python
component: teammates/backend_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-213229-40ac57
  verdict: verified
  check_ids:
  - tests
  - policy:tests_untouched
  - policy:manifests_untouched
  - policy:diff_size
  revision: ef2764b2b0ba6a4d7a660124190726a3
  rows:
  - behaviour: The backend engineer decomposed the long function into helpers and kept `summarize()` as
      the entry point, then ran the unittest suite during implementation.
    guidance: 'teammates/backend_engineer/tool_guidance: read before editing and run focused checks as
      you implement.'
    effect: The controller's verification tests passed, and the policy checks confirmed tests and manifests
      were untouched and the diff stayed within limits.
    verdict: helped
    evidence:
    - tests
    - policy:tests_untouched
    - policy:manifests_untouched
    - policy:diff_size
    - '#16'
    - '#17'
    - '#18'
    - '#19'
created: '2026-11-16T21:35:11.219411Z'
review: auto
---
# Lesson les-86349fe5 (refactor/python)

## Cue

A refactor is constrained to preserve a public function's behavior and leave the existing tests unchanged.

## Lesson

Add guidance to compare the refactored implementation against the original on representative inputs, including result ordering and edge cases, before relying on the existing suite alone.
