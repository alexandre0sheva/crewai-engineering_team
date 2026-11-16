---
id: les-942f39b4
domain: '*/python'
component: teammates/generalist_engineer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-194924-a13003
  verdict: verified
  check_ids:
  - tests
  revision: ef4922da4b216b2f2a17dfc5564ecbc6
  rows:
  - behaviour: The upgrader adapted the invoicer to moneyfmt 2.0.0 but did not preserve the previous rejection
      of CHF-prefixed input; the reviewer identified this, and the debugger reproduced `Design=CHF1` being
      accepted and rendered as EUR before adding a rejection and regression test.
    guidance: teammates/generalist_engineer/tool_guidance; the guidance to satisfy the request and run
      real checks did not explicit...
    effect: The changed behavior was caught in review and required a repair round. The controller's final
      tests passed after the repair.
    verdict: hurt
    evidence:
    - '#61'
    - '#64'
    - '#70'
    - tests
created: '2026-11-16T19:52:36.252011Z'
review: auto
---
# Lesson les-942f39b4 (*/python)

## Cue

A dependency upgrade changes a parser or formatter API, while the application must preserve its existing CLI validation and error behavior.

## Lesson

Change guidance to compare representative invalid inputs against the prior CLI behavior, not just confirm the new API works and outputs remain correct; add regression coverage for any changed rejection behavior.
