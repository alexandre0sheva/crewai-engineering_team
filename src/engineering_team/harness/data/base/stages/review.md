---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Review the finished project {project_name} in {workspace_path}. You can only read it: do
not write or change any file, and do not report anything you have not seen in the code.

Specification:

{spec}

Read the specification, then the source files, tests, configuration, and documentation
(use the search, symbol, and Git history tools to find your way), and judge the project
against your own checklist. Report each real defect once, with the file and line, a
severity (critical, high, medium, low, info) that reflects its impact on the product, and
a concrete suggested fix. Leave out style preferences and anything that is not a defect.
If you find nothing, say so with an empty list. If you ran a tool, name it and what it
reported; if a tool could not run, say that instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
