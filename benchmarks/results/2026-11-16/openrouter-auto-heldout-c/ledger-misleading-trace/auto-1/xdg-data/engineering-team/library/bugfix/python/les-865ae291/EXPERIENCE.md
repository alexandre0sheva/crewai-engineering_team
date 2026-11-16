---
id: les-865ae291
domain: bugfix/python
component: teammates/security_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-200402-6f297c
  verdict: verified
  check_ids:
  - tests
  - repro
  revision: 99d767e7233265784e26fc39c4e508b4
  rows:
  - behaviour: During the read-only security review, called Git Diff Between Refs and Git Info with invalid
      argument names, and used an unsupported 'case' value for Search Project Files.
    guidance: teammates/security_engineer/guardrails forbids changing anything and forbids claiming to
      have run a tool that was not...
    effect: 'Three failed tool calls (#92, #93, #101, #102) were wasted before the correct calls succeeded
      (#95, #96, #103, #104); the review still completed with no uncommitted changes and a findings-only
      report, and no check result regressed.'
    verdict: hurt
    evidence:
    - '#92'
    - '#93'
    - '#101'
    - '#102'
    - '#95'
    - '#96'
created: '2026-11-16T20:21:36.861209Z'
review: auto
---
# Lesson les-865ae291 (bugfix/python)

## Cue

When a teammate must review a completed change without editing it, or when a patch/grep tool rejects arguments, the tool's accepted argument names and enums are not obvious from the task description.

## Lesson

Extend teammates/security_engineer/tool_guidance with a short 'tool invocation hygiene' note stating that the diff, git-info and search tools require their documented named arguments and enumerated option values (for example the search tool's case option only accepts its listed values), and that the agent should load the tool schema or check a tool's argument validation error before retrying, rather than guessing argument names.
