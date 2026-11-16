---
id: les-0c4df4fa
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-194732-8b8bfd
  verdict: verified
  check_ids:
  - tests
  revision: 707bd7d3bb2f5b4e2d57b66e9fc738da
  rows:
  - behaviour: Lead agent worked alone and called no teammates, writing the package, tests and README
      directly (solo ladder start), instead of delegating.
    guidance: 'teammates/generalist_engineer: tool_guidance says to run real checks instead of assuming;
      no guidance covered test di...'
    effect: 'Lead''s own test run first failed (#8 exit 1), then passed after a fix (#11). Verify stage
      then hit a ''No tests ran'' failure on the official tests check (verify/tests failed, exit 5 at
      #17) because tests/ lacked __init__.py for the harness''s discover -t . invocation; fixed by adding
      tests/__init__...'
    verdict: confounded
    evidence:
    - '#8'
    - '#11'
    - '#17'
    - '#20'
    - '#21'
    - 'verify / tests: failed'
    - 'verify / tests: passed'
  - behaviour: Lead did not run the project's actual test command (discover -t .) before reporting the
      MVP built with all 18 tests passing; it only ran discover -s tests.
    guidance: 'teammates/generalist_engineer: ''never report a check as passing unless you ran it successfully''
      — the agent ran a dif...'
    effect: The reported pass did not match the controller's check; the verify stage's official tests
      check failed with 'No tests ran' and needed a repair round (Round 1) to add tests/__init__.py.
    verdict: hurt
    evidence:
    - '#8'
    - '#11'
    - '#14'
    - '#17'
    - '#21'
    - 'Repair rounds: Round 1'
  - behaviour: Verify agent diagnosed the missing package marker from the failing output, ran the check
      command directly, and added an empty tests/__init__.py.
    guidance: No guidance covered this; agent followed the real check's output.
    effect: Check tests went from failed to passed in one repair round.
    verdict: helped
    evidence:
    - '#17'
    - '#20'
    - '#21'
    - 'tests [test]: passed'
created: '2026-11-16T19:49:17.037893Z'
review: auto
---
# Lesson les-0c4df4fa (new_project/*)

## Cue

Standard-library Python package task where the project check runs unittest discovery from the project root (top-level directory set to root) and tests live in a tests/ folder

## Lesson

Add guidance to the generalist_engineer tool_guidance: before reporting tests passing, run the exact test discovery command the project check uses (e.g. python -m unittest discover -t .), and make sure tests/ has an __init__.py so that discovery finds tests.
