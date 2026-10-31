---
name: review-checklist
description: What to look for in a code review.
teammates: [code_reviewer]
---
# Review checklist

Your checklist: correctness (wrong logic, unhandled errors and edge cases, broken contracts between
modules), maintainability (confusing structure, duplication that will bite, dead code), tests
(missing coverage of the acceptance criteria, tests that assert nothing), and diff noise (unrelated
or generated files committed, leftovers, debug output).

Work through it in that order: a correctness defect outranks everything below it. For each finding,
name the file and line, say what goes wrong for a user and give the concrete fix.
