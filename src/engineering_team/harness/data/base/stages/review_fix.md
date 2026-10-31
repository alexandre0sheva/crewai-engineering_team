---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Review the FIX the team made for a bug in the existing project {project_name} in
{workspace_path}. You can only read it: do not write or change any file, and do not report
anything you have not seen in the code.

The bug report:

{requirements}

The reproduction (it failed before the fix and passes after it, as the controller saw):

{repro}

The debugger's account of the fix (not evidence):

{fix_note}

The team started from commit {base}. Read the change with Git Diff Between Refs (first
{base}, second HEAD; start with stat=true) and Git Info diff for anything not committed yet,
then read the changed files in full where the diff is not enough, and the code that calls
them. Judge the change, not the whole project: does it remove the cause or only hide the
symptom? Could it break callers or other inputs (look at the callers)? Is the regression
test meaningful, or does it only pass by accident? Is anything in the diff noise (unrelated
reformatting, renames)? Report only real defects, each once, with the file and line, a
severity (critical, high, medium, low, info) that reflects its impact, and a concrete
suggested fix. Leave out style preferences. If you find nothing, say so with an empty list.
If you ran a tool, name it and what it reported; if a tool could not run, say that instead of
guessing.

{handoff}
{card}
{notes}
{resume_note}
