---
id: les-54b33a4c
domain: docs/python
component: skills/docs-checklist
confidence: low
evidence:
  run_id: 20261116-214532-2af5f2
  verdict: verified
  check_ids:
  - tests
  - policy:docs_only
  revision: f0c780612e4de82c9be5f5047e07eb1c
  rows:
  - behaviour: The writer initially omitted the automatically available `-h`/`--help` option from the
      command reference; after the reviewer raised it, the writer updated the document to cover help at
      the global and subcommand levels.
    guidance: 'teammates/technical_writer/tool_guidance: read the code, tests, and specification, then
      verify every command and exam...'
    effect: The reviewer reported the omission as a high-severity finding, triggering a repair round.
      The writer then corrected the documentation. Controller checks passed afterward, though they do
      not independently establish that the help documentation is complete.
    verdict: hurt
    evidence:
    - '#45'
    - '#52'
    - '#62'
    - tests
    - policy:docs_only
created: '2026-11-16T21:48:22.607326Z'
review: auto
---
# Lesson les-54b33a4c (docs/python)

## Cue

A CLI reference documents commands and arguments but may overlook options automatically supplied by its argument parser.

## Lesson

Add an explicit completeness check for parser-provided help options, including global and subcommand help, to the documentation checklist.
