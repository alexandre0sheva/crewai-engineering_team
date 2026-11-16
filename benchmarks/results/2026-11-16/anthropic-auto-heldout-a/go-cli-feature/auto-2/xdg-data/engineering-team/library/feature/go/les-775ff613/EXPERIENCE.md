---
id: les-775ff613
domain: feature/go
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-194939-4ead32
  verdict: verified
  check_ids:
  - tests
  revision: d2c19b6bdd4f433b297aa9678f06776e
  rows:
  - behaviour: 'Agent made edits to main.go and count.go from memory after failed patch and replace calls
      (#2, #5, #6), then re-read files before rewriting; patch/replace failures were recovered by full-file
      writes.'
    guidance: ''
    effect: 'Several wasted calls; the baseline-style test run then failed (#15-#17 exit 1) and needed
      further repair rounds before go test passed at #31.'
    verdict: hurt
    evidence:
    - '#2'
    - '#5'
    - '#6'
    - '#15'
    - '#16'
    - '#17'
    - '#31'
  - behaviour: 'Agent wrote the test expectation for the JSON/filter case wrongly (ordering of été vs
      cat with equal counts), then flip-flopped the expected order in main_test.go (#13, #27, #30) before
      the suite passed.'
    guidance: ''
    effect: 'Test failures at #25 and #29 led to repeated edits; final go test passed only at #31 after
      the expectation was corrected to match the tie-break order.'
    verdict: hurt
    evidence:
    - '#13'
    - '#25'
    - '#27'
    - '#29'
    - '#30'
    - '#31'
  - behaviour: Agent tried to run gofmt directly, which the command allowlist rejected (#21), instead
      of using go fmt through the allowed go command.
    guidance: ''
    effect: One wasted call; no check failed because of it, and formatting was not part of the required
      checks.
    verdict: no observed effect
    evidence:
    - '#21'
created: '2026-11-16T19:52:33.769018Z'
review: auto
---
# Lesson les-775ff613 (feature/go)

## Cue

Small Go CLI feature addition with new flags and table-driven tests, where the agent edits several files in one pass

## Lesson

Under teammates/generalist_engineer tool_guidance, add: re-read each target file immediately before any replace or patch, and derive expected test outputs from the implemented ordering rule rather than hand-writing them first; run go test after each file group, not only at the end.
