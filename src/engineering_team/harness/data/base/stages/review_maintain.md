---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Review the CHANGE the team made to the existing project {project_name} in {workspace_path}
as a maintenance task. You can only read it: do not write or change any file, and do not
report anything you have not seen in the code.

The task:

{requirements}

The team started from commit {base}. Read the change with Git Diff Between Refs (first {base},
second HEAD; start with stat=true) and Git Info diff for anything not committed yet, then the
changed files in full where the diff is not enough. Judge the change against the task: does
it do what was asked and nothing else? Does it keep behaviour (a refactor) or pin it (tests)?
Is anything in the diff noise (unrelated reformatting, renames, files nobody asked for)?
Report only real defects, each once, with the file and line, a severity (critical, high,
medium, low, info) that reflects its impact, and a concrete suggested fix. Leave out style
preferences. If you find nothing, say so with an empty list. If you ran a tool, name it and
what it reported; if a tool could not run, say that instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
