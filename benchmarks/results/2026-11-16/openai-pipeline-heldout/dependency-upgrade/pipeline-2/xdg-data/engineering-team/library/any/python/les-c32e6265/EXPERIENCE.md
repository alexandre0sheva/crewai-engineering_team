---
id: les-c32e6265
domain: '*/python'
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-200851-e1c4dd
  verdict: verified
  check_ids:
  - tests
  revision: c437736d83d773e5dd97abca644b5457
  rows:
  - behaviour: The implementation relied on the new formatter’s acceptance of parsed inputs without checking
      that the invoicer still rejected a CHF-prefixed amount; the reviewer flagged this and the repair
      added validation and a CLI regression test.
    guidance: teammates/generalist_engineer/tool_guidance (read before editing and satisfy the request);
      the request’s requirement...
    effect: The debugger reproduced `Design=CHF10` being accepted, then repaired the behavior. Controller
      tests passed after repair (4 passed).
    verdict: hurt
    evidence:
    - '#85'
    - '#88'
    - '#91'
    - '#92'
    - tests
created: '2026-11-16T20:13:23.612979Z'
review: auto
---
# Lesson les-c32e6265 (*/python)

## Cue

A dependency upgrade changes parsing or formatting APIs in a CLI that promises to preserve existing input errors and output behavior.

## Lesson

Change guidance to compare representative invalid-input behavior with the pre-upgrade CLI and add a regression test for any changed acceptance or error path.
