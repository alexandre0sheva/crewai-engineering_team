---
id: les-a8d972f7
domain: docs/python
component: teammates/technical_writer/tool_guidance
confidence: low
evidence:
  run_id: 20261116-203117-c884b7
  verdict: verified
  check_ids:
  - tests
  - policy:docs_only
  revision: 8650670006c280ac8297c8dd375c687b
  rows:
  - behaviour: The writer initially documented examples that the reviewer found did not work in a fresh
      directory, then revised docs/cli.md in response.
    guidance: 'teammates/technical_writer/tool_guidance: verify every documented command and example against
      actual project behavior.'
    effect: A review repair round was required. The writer reported the issue fixed; the controller's
      tests and documentation-only policy checks passed, though no recorded check specifically executes
      the examples.
    verdict: hurt
    evidence:
    - '#39'
    - '#45'
    - '#56'
    - tests
    - policy:docs_only
created: '2026-11-16T20:33:59.522298Z'
review: auto
---
# Lesson les-a8d972f7 (docs/python)

## Cue

A documentation request requires several CLI examples to work sequentially in a fresh directory, and the harness offers no direct terminal tool for trying them.

## Lesson

Change the guidance to require a concrete, recorded verification path for sequential examples (or mark them unchecked when execution is unavailable), rather than relying on review to discover fresh-directory failures.
