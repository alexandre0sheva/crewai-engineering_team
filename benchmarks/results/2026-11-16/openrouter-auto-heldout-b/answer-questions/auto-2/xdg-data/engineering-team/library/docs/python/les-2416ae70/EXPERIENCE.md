---
id: les-2416ae70
domain: docs/python
component: stages/docs
confidence: medium
evidence:
  run_id: 20261116-192241-f4c8ab
  verdict: verified
  check_ids:
  - policy:docs_only
  - tests
  revision: d26c66f27292e1bb52ddb14f3f9ecd56
  rows:
  - behaviour: 'The writer produced the required ANSWERS.md with one section per question and code citations,
      while also editing README.md, docs/usage.md, and CHANGELOG.md during the docs stage (e.g. #115-#125).'
    guidance: teammates/technical_writer/tool_guidance and guardrails (documentation files only)
    effect: 'The reviewer confirmed only documentation files were changed and no code was touched (diff
      stat: ANSWERS.md, CHANGELOG.md, README.md, docs/usage.md); the docs-only policy check passed. The
      extra scope was not penalised, though it was not requested.'
    verdict: helped
    evidence:
    - '#115'
    - '#116'
    - '#117'
    - '#118'
    - '#119'
    - '#120'
    - '#121'
    - '#124'
created: '2026-11-16T19:35:34.840158Z'
review: auto
---
# Lesson les-2416ae70 (docs/python)

## Cue

A documentation-stage request asks for one new answers file only, but the docs recipe also updates the README/usage guide and changelog, expanding the change beyond the requested file.

## Lesson

Change the docs stage to scope edits to the explicitly requested output file (e.g. create ANSWERS.md only) unless the request asks for broader documentation updates; keep reviewer checks that no code files were modified.
