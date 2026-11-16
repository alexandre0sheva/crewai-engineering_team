---
id: les-7e4fe956
domain: refactor/python
component: teammates/backend_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-200402-d8007d
  verdict: verified
  check_ids:
  - tests
  - policy:tests_untouched
  - policy:diff_size
  revision: 75a78be89586932174197da1fa91663f
  rows:
  - behaviour: Backend engineer rewrote orders/summary.py in one write, splitting summarize() into helper
      functions, then ran the unittest suite (exit 0) as a focused check.
    guidance: ''
    effect: Tests passed on the first run; verify/tests and tests check passed with 3 of 3; policy checks
      for tests_untouched, manifests_untouched and diff_size all passed.
    verdict: helped
    evidence:
    - '#13'
    - '#15'
    - tests
    - policy:tests_untouched
    - policy:diff_size
  - behaviour: Code reviewer produced findings from reading the diff but did not run the tests; it reported
      no defects.
    guidance: teammates/code_reviewer guardrails (never claim to have run a test you did not run)
    effect: Review stage succeeded; no reviewer-raised defect was contradicted by checks, and the tests
      check still passed. Behaviour equivalence was not independently exercised beyond the existing 3
      tests.
    verdict: no observed effect
    evidence:
    - '#22'
    - '#20'
    - tests
created: '2026-11-16T20:05:37.308050Z'
review: auto
---
# Lesson les-7e4fe956 (refactor/python)

## Cue

Behaviour-preserving refactor of a single long function in a small Python package, where the existing unittest suite is the only safety net

## Lesson

Add to the backend_engineer tool_guidance: before writing the refactored module, record the exact output (key order, rounding, problem ordering) of the original function on a few representative inputs, and after editing compare against it, since the existing tests may not pin these details.
