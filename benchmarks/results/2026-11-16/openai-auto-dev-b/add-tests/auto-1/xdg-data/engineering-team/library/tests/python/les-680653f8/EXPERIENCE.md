---
id: les-680653f8
domain: tests/python
component: teammates/quality_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-214256-e70271
  verdict: verified
  check_ids:
  - tests
  revision: e571ff444d90bf78e5e255cee6d926ce
  rows:
  - behaviour: Initially created tests/test_words.py without making tests importable for the default discovery
      mode, then added an empty tests/__init__.py after verification showed discovery needed it.
    guidance: 'teammates/quality_engineer tool_guidance: use unittest and cover behavior; the guidance
      did not specify package disco...'
    effect: The controller’s requested discovery command first ran no tests and failed; after repair,
      it passed with 29 tests. The later discovery command using -t . also passed.
    verdict: hurt
    evidence:
    - tests
    - '#22'
    - '#23'
    - '#34'
    - '#35'
created: '2026-11-16T21:45:31.120322Z'
review: auto
---
# Lesson les-680653f8 (tests/python)

## Cue

A Python unittest task requires tests to be found by a specified discovery command, especially when discovery modes or package boundaries differ.

## Lesson

Change the test-writing guidance to run the exact requested discovery command before declaring the suite complete, and add package markers only when that command requires them.
